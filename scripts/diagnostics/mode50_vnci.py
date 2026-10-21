"""Source mode50 framing over owned VNCI RAW CAN primitives; read-only PDUs."""
from copy import deepcopy

from .allowlist import blocked_reason
from .capture_clock import capture_timestamp
from .elm import ElmError
from .mode50_transport import ReplyAssembler, request_frame, FLOW_CONTROL, RX_ID, TX_ID
from .pair import response_matches
from .vnci import DpuClient, UNDEF, ID_UNDEF, _result_indication


class Mode50DpuClient(DpuClient):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.receive_address=None
        self._raw_active=set()
        self._raw_tags={}

    def configure_manufacturer(self,profile,selected,deadline,*,identity_only=False):
        from .manufacturer_transport import prepared_read_contract
        self._guard(deadline)
        self._configured=False;self.allowed=set();self._manufacturer_contract=None
        contract=prepared_read_contract(profile,selected,transport_kind='d-pdu-iso-tp',identity_only=identity_only)
        if (self._device_brand!='vnci' or contract['ecuId']!=88 or contract['txId']!=TX_ID or contract['rxId']!=RX_ID
                or contract.get('transportAddressing',{}).get('kind')!='porsche-mode50'):
            raise ElmError('manufacturer-transport-unqualified')
        try:self.native.create_mode50_link()
        except Exception:self._poisoned=True;raise
        self.receive_address=None
        self.allowed={r['requestHex'] for r in contract['groups']}|{r['requestHex'] for r in contract['identityRequests']}
        self.allowed.update(contract['sourceCommands']);self.allowed.add(contract['session']['requestHex'])
        self._manufacturer_contract=deepcopy(contract) if identity_only else None
        self._configured=True

    def _start_raw(self,frame=None,*,deadline):
        self._guard(deadline)
        result=self.native.start_mode50_primitive(frame)
        if not isinstance(result,tuple) or len(result)!=2:
            raise ElmError('d-pdu:primitive-handle-missing')
        cop,tag=result
        if (type(cop) is not int or not 0<=cop<ID_UNDEF or cop in self._raw_active
                or type(tag) is not int or not 0<tag<=0xFFFFFFFF):
            raise ElmError('d-pdu:primitive-handle-missing')
        self._raw_active.add(cop)
        self._raw_tags[cop]=tag
        return cop

    def _raw_event(self,event,*,during_cleanup=False):
        cop=event.get('cop')
        if (type(cop) is not int or cop not in self._raw_active
                or type(event.get('tag')) is not int or event['tag']!=self._raw_tags[cop]):
            raise ElmError('d-pdu:foreign-com-primitive')
        row=dict(layer='can',cop=cop,tag=event['tag'],eventType=event.get('type'),duringCleanup=during_cleanup)
        if 'timestamp' in event:row['nativeTimestampUsRaw']=event['timestamp']
        if event.get('type')==0x1300:
            row.update(hex=event.get('payload',b'').hex().upper(),flagsHex=event.get('flags',b'').hex().upper())
            row.update(uniqueId=event.get('unique_id'),acceptanceId=event.get('acceptance_id'))
        else:row['value']=event.get('value')
        self.raw_log.append(row)
        if event.get('type')==0x1301 and event.get('value') in (0x8012,0x8013):
            self._raw_active.remove(cop)
            self._raw_tags.pop(cop)

    def _cancel_raw(self):
        """Cancellation is complete only after every owned COP is terminal."""
        for cop in list(self._raw_active):
            try:self.native.cancel(cop)
            except Exception as error:self._cleanup_errors.append('d-pdu:primitive-cancel-failed:'+str(error))
        deadline=self._clock()+1.0
        try:
            for _ in range(4096):
                if not self._raw_active:return
                if self._clock()>=deadline:raise ElmError('d-pdu:mode50-primitive-terminal-timeout')
                event=self.native.event()
                if event is None:self._sleep(.01);continue
                self._raw_event(event,during_cleanup=True)
                if event['type']==0x1301 and event['value'] not in (0x8010,0x8011,0x8012,0x8013):
                    raise ElmError('d-pdu:cleanup-unexpected-state')
                if event['type'] not in (0x1300,0x1301):raise ElmError('d-pdu:cleanup-unexpected-event')
            raise ElmError('d-pdu:cleanup-event-cap')
        except Exception as error:self._cleanup_errors.append(str(error))

    def request(self,req_hex,deadline):
        self._guard(deadline)
        if not self._configured:raise ElmError('not-configured')
        try:request=bytes.fromhex(req_hex)
        except (ValueError,TypeError):raise ElmError('invalid-request-hex') from None
        reason=blocked_reason(request,self.allowed)
        if reason:raise ElmError(reason)
        frame=request_frame(request)
        if not self._lock.acquire(blocking=False):raise ElmError('request-in-flight')
        try:
            self._guard(deadline)
            for _ in range(32):
                event=self.native.event()
                if event is None:break
                if (event.get('type')!=0x1301 or event.get('cop')!=UNDEF
                        or event.get('value') not in (0x8050,0x8051)):
                    raise ElmError('desynchronized-rx')
            else:raise ElmError('d-pdu:setup-event-cap')
            rx=self._start_raw(deadline=deadline)
            tx=self._start_raw(frame,deadline=deadline)
            assembler=ReplyAssembler(self.receive_address)
            pending=[];final=None;started=None;stopping=False
            for _ in range(4096):
                self._guard(deadline)
                event=self.native.event()
                if event is None:self._sleep(.01);continue
                self._raw_event(event)
                cop=event['cop'];kind=event['type']
                if kind==0x1302:raise ElmError('d-pdu:event-error:0x'+format(event['value'],'X'))
                if kind==0x1301:
                    value=event['value']
                    if value not in (0x8010,0x8011,0x8012,0x8013):raise ElmError('d-pdu:unexpected-primitive-state')
                    if cop==tx and value==0x8013:raise ElmError('cancelled')
                    if cop==rx and value in (0x8012,0x8013) and not stopping:
                        raise ElmError('d-pdu:mode50-receive-ended')
                elif kind==0x1300:
                    data=event.get('payload');flags=event.get('flags')
                    indication=_result_indication(data,flags) if isinstance(data,bytes) and isinstance(flags,bytes) else None
                    if indication is not None:
                        if event.get('unique_id') not in (1,UNDEF,ID_UNDEF):raise ElmError('d-pdu:malformed-or-unmatched-response')
                    else:
                        if (cop!=rx or final is not None or type(data) is not bytes or not 7<=len(data)<=12
                                or int.from_bytes(data[:4],'big')!=RX_ID or flags!=b'\0'*4
                                or event.get('unique_id')!=1 or event.get('acceptance_id')!=1
                                or event.get('extra_header') not in (None,b'',data[:4])
                                or event.get('extra_footer') not in (None,b'')):
                            raise ElmError('d-pdu:malformed-or-unmatched-response')
                        stamp=capture_timestamp()
                        if started is None:started=stamp
                        payload=assembler.feed(data[4:])
                        if payload is not None:
                            if self._pdu_sink:self._pdu_sink(dict(requestHex=req_hex.upper(),pduHex=payload.hex().upper(),
                                **stamp,startedTimestampUs=started['timestampUs'],startedMonotonicTimestampUs=started['monotonicTimestampUs'],
                                timestampSource='host-pdu-observed',transportKind='d-pdu-mode50',observation='reassembled-complete-pdu',
                                primitive=rx,expectedPrimitive=rx,uniqueId=1,flagsHex=flags.hex().upper(),duringCleanup=False))
                            relation=response_matches(request,payload)
                            if relation is None or request==b'\x3E\0' and payload[0]!=0x7F and payload!=b'\x7E\0':
                                raise ElmError('wrong-sid-or-did-echo')
                            self.receive_address=assembler.receive_address
                            if relation=='pending-7Fxx78':
                                pending.append(payload.hex().upper())
                                if len(pending)>256:raise ElmError('mode50-pending-limit')
                                assembler=ReplyAssembler(self.receive_address);started=None
                            else:final=dict(ok=relation!='negative',error='negative-response' if relation=='negative' else None,
                                            payload_hex=payload.hex().upper(),pending=pending)
                else:raise ElmError('d-pdu:unexpected-primitive-event')
                # FC writes never replay the diagnostic request. Wait for the
                # previous transmit to finish, then track this FC as its own COP.
                if tx not in self._raw_active and assembler.flow_control_needed:
                    tx=self._start_raw(FLOW_CONTROL,deadline=deadline);assembler.flow_control_sent()
                if final is not None and tx not in self._raw_active and not stopping:
                    self.native.cancel(rx);stopping=True
                if stopping and not self._raw_active:return final
            raise ElmError('d-pdu:request-event-cap')
        except Exception:
            self._poisoned=True
            raise
        finally:
            if self._raw_active:self._cancel_raw()
            self._lock.release()
