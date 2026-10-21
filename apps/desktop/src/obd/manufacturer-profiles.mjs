/** Explicit supported source routes shared by desktop dispatch and parameter selection. */
const READ_PROFILES = new Map([
  ["9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5", 1],
  ["9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1", 9],
]);
const LIVE_READ_PROFILES = new Map([...READ_PROFILES, ...[
  'PDK', 'PDK_ohne_Quersperre', 'PDK_A2', 'PDK_A2_ohne_Quersperre',
  'PDK_A3', 'PDK_A3_ohne_Quersperre', 'PDK_A4', 'PDK_A5_CS',
].map(name => ['9x1:Getriebesteuerung_PDK:' + name, 2])]);
for (const name of ['PSM_a1','PSM_a2','PSM_a2_1','PSM_a2_2','PSM_a2_3','PSM_a2_5','PSM_a2_6','PSM_a2_7_CS']) LIVE_READ_PROFILES.set('9x1:PSM:'+name,5);
for (const name of ['Basis_GT4','Basis_GT5','Basis_VR10','Basis_VR12','Max_GT4','Max_GT5','Max_VR10','Max_VR12']) LIVE_READ_PROFILES.set('9x1:BCM_vorne:BCM_vorne_9x1_'+name,32);
for (const name of ['GT4','GT5','VR10','VR12']) LIVE_READ_PROFILES.set('9x1:BCM_hinten:BCM_hinten_9x1_'+name,35);
LIVE_READ_PROFILES.set('9x1:BCM_vorne:BCM_vorne_unprogrammiertes_SG',32);
LIVE_READ_PROFILES.set('9x1:BCM_hinten:BCM_hinten_uncodiertes_SG',35);

for (const [ecu,prefix,names] of [[54,'9x1:Lenkstockschalter:Kombilenkstockmodul_',['A2_4','A2_5','A2_6']],[18,'9x1:PDC:PDC_',['4_Kanal_A2_4','4_Kanal_A2_5','4_Kanal_A2_7','8_Kanal_A2_4','8_Kanal_A2_5','8_Kanal_A2_7']],[50,'9x1:EPB:',['Parkbremse_a7']]]) for (const name of names) LIVE_READ_PROFILES.set(prefix+name,ecu);
LIVE_READ_PROFILES.set('9x1:Waehlhebel:WH',68);
LIVE_READ_PROFILES.set('9x1:RDK:Reifendruckkontrolle_A2_4',55);
LIVE_READ_PROFILES.set('9x1:RDK:RDK_unprogrammiertes_SG',55);
for (const name of ['A2_6','A2_7','A2_8','A2_9','A3_1','A3_2','A3_3','A3_4','A3_5','A3_6']) LIVE_READ_PROFILES.set('9x1:Kombiinstrument_MM:Kombiinstrument_'+name,8);
LIVE_READ_PROFILES.set("9x1:Airbag:Airbag_A2_4_9x1",4);
LIVE_READ_PROFILES.set("9x1:Airbag:Airbag_A2_5_9x1",4);
LIVE_READ_PROFILES.set("9x1:Airbag:Airbag_A2_6_9x1",4);
LIVE_READ_PROFILES.set("9x1:Airbag:Airbag_A2_7",4);
LIVE_READ_PROFILES.set("9x1:Airbag:Airbag_A2_8",4);
LIVE_READ_PROFILES.set("9x1:BKE:BKE_1_Zone_A2_1",7);
LIVE_READ_PROFILES.set("9x1:BKE:BKE_1_Zone_A2_2",7);
LIVE_READ_PROFILES.set("9x1:BKE:BKE_1_Zone_A2_3",7);
LIVE_READ_PROFILES.set("9x1:BKE:BKE_1_Zone_A2_4",7);
LIVE_READ_PROFILES.set("9x1:BKE:BKE_2_Zonen_A2_1",7);
LIVE_READ_PROFILES.set("9x1:BKE:BKE_2_Zonen_A2_2",7);
LIVE_READ_PROFILES.set("9x1:BKE:BKE_2_Zonen_A2_3",7);
LIVE_READ_PROFILES.set("9x1:BKE:BKE_2_Zonen_A2_4",7);
LIVE_READ_PROFILES.set("9x1:BKE:BKE_2_Zonen_A2_5",7);
LIVE_READ_PROFILES.set("9x1:BKE:Heizung_1_Zone_A2_3",7);
LIVE_READ_PROFILES.set("9x1:BKE:Heizung_1_Zone_A2_4",7);
LIVE_READ_PROFILES.set("9x1:BKE:Klima_2_Zonen_A2_10",7);
LIVE_READ_PROFILES.set("9x1:PASM_PADM:PADM_9x1_a2_1",19);
LIVE_READ_PROFILES.set("9x1:PASM_PADM:PADM_9x1_a2_2",19);
LIVE_READ_PROFILES.set("9x1:PASM_PADM:PADM_9x1_a2_2_GT",19);
LIVE_READ_PROFILES.set("9x1:PASM_PADM:PASM_9x1_a2_1",19);
LIVE_READ_PROFILES.set("9x1:PASM_PADM:PASM_9x1_a2_2",19);
LIVE_READ_PROFILES.set("9x1:PASM_PADM:PASM_9x1_a2_2_GT",19);
LIVE_READ_PROFILES.set("9x1:PASM_PADM:PASM_PADM_9x1_a2_1",19);
LIVE_READ_PROFILES.set("9x1:PASM_PADM:PASM_PADM_9x1_a2_2",19);
LIVE_READ_PROFILES.set("9x1:PASM_PADM:PASM_PADM_9x1_a2_2_GT",19);
LIVE_READ_PROFILES.set("9x1:Steering_Control_Unit:EPS_A2_2",69);
LIVE_READ_PROFILES.set("9x1:Steering_Control_Unit:EPS_A2_3",69);
LIVE_READ_PROFILES.set("9x1:Steering_Control_Unit:EPS_A2_4",69);
LIVE_READ_PROFILES.set("9x1:Steering_Control_Unit:EPS_A2_5",69);
LIVE_READ_PROFILES.set("9x1:Steering_Control_Unit:EPS_A2_GT",69);
LIVE_READ_PROFILES.set("9x1:Zusatzinstrument:Analoge_Uhr_A2",29);
LIVE_READ_PROFILES.set("9x1:Zusatzinstrument:Analoge_Uhr_A2_1",29);
LIVE_READ_PROFILES.set("9x1:Zusatzinstrument:Analoge_Uhr_A2_2",29);
LIVE_READ_PROFILES.set("9x1:Zusatzinstrument:Analoge_Uhr_A2_3",29);
LIVE_READ_PROFILES.set("9x1:Zusatzinstrument:Zusatzinstrument_A2_0",29);
LIVE_READ_PROFILES.set("9x1:Zusatzinstrument:Zusatzinstrument_A2_1",29);
LIVE_READ_PROFILES.set("9x1:Zusatzinstrument:Zusatzinstrument_A2_2",29);
LIVE_READ_PROFILES.set("9x1:Zusatzinstrument:Zusatzinstrument_A2_3",29);
LIVE_READ_PROFILES.set("9x1:Kombiinstrument_MM:Kombiinstrument_A2_2",8);
LIVE_READ_PROFILES.set("9x1:Kombiinstrument_MM:Kombiinstrument_A2_3",8);
LIVE_READ_PROFILES.set("9x1:Kombiinstrument_MM:Kombiinstrument_A2_4",8);
LIVE_READ_PROFILES.set("9x1:Kombiinstrument_MM:Kombiinstrument_A2_5",8);
LIVE_READ_PROFILES.set("9x1:Head_Unit:CDR31",70);
LIVE_READ_PROFILES.set("9x1:Head_Unit:PCM31_A3",70);
LIVE_READ_PROFILES.set("9x1:Head_Unit:PCM31_A3_2",70);
LIVE_READ_PROFILES.set("9x1:Head_Unit:PCM31_A3_3",70);
LIVE_READ_PROFILES.set("9x1:Head_Unit:PCM31_G1_und_9x1",70);
LIVE_READ_PROFILES.set("9x1:TVTuner:TVTuner_A2",75);
LIVE_READ_PROFILES.set("9x1:TVTuner:TVTuner_Delphi_A2",75);
LIVE_READ_PROFILES.set("9x1:Verstaerker:ASK",65);
LIVE_READ_PROFILES.set("9x1:Verstaerker:BOSE",65);
LIVE_READ_PROFILES.set("9x1:ACC3:ACC3_A2",64);
LIVE_READ_PROFILES.set("9x1:ACC3:ACC3_A3",64);
LIVE_READ_PROFILES.set("9x1:ACC3:ACC3_A4",64);
LIVE_READ_PROFILES.set("9x1:ACC3:ACC3_A5",64);
LIVE_READ_PROFILES.set("9x1:ACC3:ACC3_A6",64);
LIVE_READ_PROFILES.set("9x1:ACC3:ACC3_A7",64);
LIVE_READ_PROFILES.set("9x1:ACC3:ACC3_A8",64);
LIVE_READ_PROFILES.set("9x1:Frontkamera:Frontkamera_0004",74);
LIVE_READ_PROFILES.set("9x1:Frontkamera:Frontkamera_0005",74);
LIVE_READ_PROFILES.set("9x1:Frontkamera:Frontkamera_0006",74);
LIVE_READ_PROFILES.set("9x1:Frontkamera:Frontkamera_1",74);
LIVE_READ_PROFILES.set("9x1:Frontkamera:Frontkamera_2",74);
LIVE_READ_PROFILES.set("9x1:Frontkamera:Frontkamera_P005",74);
LIVE_READ_PROFILES.set("9x1:Frontkamera:Frontkamera_P006",74);
LIVE_READ_PROFILES.set("9x1:Frontkamera:Frontkamera_PP06",74);
LIVE_READ_PROFILES.set("9x1:Rueckfahrkamera_II:RFK_Compact_A1_1",81);
LIVE_READ_PROFILES.set("9x1:Rueckfahrkamera_II:RFK_Compact_A2_1",81);
LIVE_READ_PROFILES.set("9x1:Rueckfahrkamera_II:RFK_Mid_A1_1",81);
LIVE_READ_PROFILES.set("9x1:ERA_GLONASS:EV_ERAGLONAPeike_001",165);
LIVE_READ_PROFILES.set("9x1:ERA_GLONASS_VFA:EV_ERAGLONAVFA_001",165);
LIVE_READ_PROFILES.set("9x1:Verdeck_Synchronisation:Verdeck_Synchronisations_SG_A1_0",39);
for (const name of ['Actuator_For_Exterior_Noise_UDS','EV_SoundActuaGen2AU64X_003']) LIVE_READ_PROFILES.set('9x1:BV_ActuaForStrucBorneSoundUDS:'+name,87);
LIVE_READ_PROFILES.set("9x1:DME_BDE_Continental:SDI9_1_981_2_7L_E4E",1);
LIVE_READ_PROFILES.set("9x1:DME_BDE_Continental:SDI9_1_981_2_7L_EU4",1);
LIVE_READ_PROFILES.set("9x1:DME_BDE_Continental:SDI9_1_981_2_7L_EU5",1);
LIVE_READ_PROFILES.set("9x1:DME_BDE_Continental:SDI9_1_981_2_7L_ULEV",1);
LIVE_READ_PROFILES.set("9x1:DME_BDE_Continental:SDI9_1_981_3_4L_E4E",1);
LIVE_READ_PROFILES.set("9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU4",1);
LIVE_READ_PROFILES.set("9x1:DME_BDE_Continental:SDI9_1_981_3_4L_ULEV",1);
LIVE_READ_PROFILES.set("9x1:DME_BDE_Continental:SDI9_1_981_3_8L_E4E",1);
LIVE_READ_PROFILES.set("9x1:DME_BDE_Continental:SDI9_1_981_3_8L_EU4",1);
LIVE_READ_PROFILES.set("9x1:DME_BDE_Continental:SDI9_1_981_3_8L_EU6",1);
LIVE_READ_PROFILES.set("9x1:DME_BDE_Continental:SDI9_1_981_3_8L_ULEV",1);
LIVE_READ_PROFILES.set("9x1:DME_BDE_Continental:SDI9_1_981_3_4L_EU5",1);
const SHARED_DME_FACTORY_PROFILE = "9x1:DME_BDE_Continental:SDI9_1_Werksdatensatz";
const SHARED_DME_UNPROGRAMMED_PROFILE = "9x1:DME_BDE_Continental:SDI_9_1_unprogrammiert";
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A3",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A4",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A4_1",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A4_2",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A5",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A5_1",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A5_2",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A5_3",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A6",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A6_1",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A6_2",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A6_3",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A6_4",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_0",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_2_China",9);
LIVE_READ_PROFILES.set("9x1:CAN_CAN_Gateway:CAN_CAN_Gateway_A7_1",9);
LIVE_READ_PROFILES.set("9x1:BV_InforContrUnit1UDS:EV_MUHig6C3Gen2AW7_001",70);
LIVE_READ_PROFILES.set("9x1:BV_InforContrUnit1UDS:EV_MUHig6C3Gen2HBAS_001",70);
LIVE_READ_PROFILES.set("9x1:BV_InforContrUnit1UDS:EV_MUStd6C3DELP_001",70);
LIVE_READ_PROFILES.set("9x1:BV_InforContrUnit1UDSDesay:EV_MU_ChinaPCM_DESAY_001",70);
LIVE_READ_PROFILES.set("9x1:BV_SoundSysteUDS:EV_AMPMst16C1Gen2ASK_001",65);
LIVE_READ_PROFILES.set("9x1:BV_SoundSysteUDS:EV_AMPMst16C4Gen2BOSE_001",65);
LIVE_READ_PROFILES.set("9x1:BV_TVTunerUDS:EV_TVMELC_001",75);
for (const i of [1,2,3,4]) LIVE_READ_PROFILES.set(`9x1:SCHEINWERFER:SCHEINWERFER_${i}`,88);
for (const name of ['RDK3_004', 'RDK3_005', 'RDK3_006', 'RDK3_007', 'RDK3_CS', 'RDK3_GT']) LIVE_READ_PROFILES.set('9x1:RDK3:'+name,55);
const DME_UDS_READ_PROFILES = new Set(["9x1:BV_ECM_UDS:Boxster_B4T_2_0L_EU6AG_mOPF", "9x1:BV_ECM_UDS:Boxster_B4T_2_0L_RDW", "9x1:BV_ECM_UDS:Boxster_B4T_2_0L_USA", "9x1:BV_ECM_UDS:Boxster_B4T_2_5L_EU6AG_mOPF", "9x1:BV_ECM_UDS:Boxster_B4T_2_5L_GTS_EU6AG_mOPF", "9x1:BV_ECM_UDS:Boxster_B4T_2_5L_GTS_RDW", "9x1:BV_ECM_UDS:Boxster_B4T_2_5L_GTS_USA", "9x1:BV_ECM_UDS:Boxster_B4T_2_5L_RDW", "9x1:BV_ECM_UDS:Boxster_B4T_2_5L_USA", "9x1:BV_EnginContrModul1UDS:EV_ECM40FSI011982906033AM_001", "9x1:BV_EnginContrModul1UDS:EV_ECM40TFS011982906033G_002", "9x1:BV_EnginContrModul1UDS:EV_ECM40TFS011982906033J_001", "9x1:BV_EnginContrModul1UDS:EV_ECM40TFS011982906033L_001", "9x1:BV_EnginContrModul1UDS:EV_ECM40TFS011982906034AC_001", "9x1:BV_EnginContrModul1UDS:EV_ECM40TFS011982906034G_001", "9x1:BV_EnginContrModul1UDS:EV_ECM40TFS011982906034J_002", "9x1:BV_EnginContrModul1UDS:EV_ECM40TFS011982906034L_001"]);
DME_UDS_READ_PROFILES.add('9x1:BV_EnginContrModul1UDS:EV_ECM40FSI0119GT906033AL_001');
DME_UDS_READ_PROFILES.add('9x1:BV_EnginContrModul1UDS:EV_ECM40FSI0119GT906033A_001');
DME_UDS_READ_PROFILES.add('9x1:BV_EnginContrModul1UDS:EV_ECM40FSI0119GT906033_001');
for (const profileId of DME_UDS_READ_PROFILES) LIVE_READ_PROFILES.set(profileId,1);
LIVE_READ_PROFILES.set(SHARED_DME_FACTORY_PROFILE,1);
LIVE_READ_PROFILES.set(SHARED_DME_UNPROGRAMMED_PROFILE,1);
LIVE_READ_PROFILES.set('9x1:BV_EnginContrModul1UDS:EV_Unprogrammiertes_SG',1);
const SHARED_READ_TARGETS = new Map(['EV_RearRadarS1BoschPO416_001','EV_RearRadarS1BoschPO67x_001',
  'EV_RearRadarS2BoschPO416_001','EV_RearRadarS2BoschPO67x_001']
  .map(name => ['9x1:BV_ContrUnitLaneChangAssis2UDS:' + name, [95,96]]));
for (const name of ['Sitz_A2','Sitz_A2_1_9x1','Sitz_A2_9x1']) SHARED_READ_TARGETS.set('9x1:Sitzsteuergeraet:'+name,[76,78]);
for (const name of ['Tuer_Vorne_A2_1_9x1','Tuer_Vorne_A2_2']) SHARED_READ_TARGETS.set('9x1:TUER_VORNE:'+name,[30,31]);
for (const role of ['Master','Slave']) for (const equipment of ['mit_AFS','ohne_AFS'])
  for (const revision of ['1310','VR06','VR15']) {
    const profileId = `9x1:SWSG:SWSG_${role}_${equipment}_ab_${revision}`;
    SHARED_READ_TARGETS.set(profileId,[47,48]);
  }
for (const [profileId, targets] of SHARED_READ_TARGETS) LIVE_READ_PROFILES.set(profileId, targets[0]);
const BOSE_PIWIS_PROFILE = '9x1:BV_SoundSysteUDS:EV_AMPMst16C4Gen2BOSE_002';
LIVE_READ_PROFILES.set(BOSE_PIWIS_PROFILE,65);
const liveReadTargets = profileId => SHARED_READ_TARGETS.get(profileId)
  || (LIVE_READ_PROFILES.has(profileId) ? [LIVE_READ_PROFILES.get(profileId)] : []);
const profileFitsGeneration = (profileId, generation) => LIVE_READ_PROFILES.has(profileId) && (generation === '982'
  ? !profileId.startsWith('9x1:DME_BDE_Continental:') || profileId === SHARED_DME_FACTORY_PROFILE || profileId === SHARED_DME_UNPROGRAMMED_PROFILE
  : generation === '981' && !DME_UDS_READ_PROFILES.has(profileId));

// These exact source states have identity/fault definitions but no measurements.
// They must never become live parameter clients or change the live version count.
const FAULT_ONLY_PROFILES = new Map([
  ...['DK2F','DK3','Links','Rechts'].map(name => [`9x1:SCHEINWERFER_LED:Scheinwerfer_LED_${name}_001`,47]),
  ['9x1:Sitzsteuergeraet:Unprogrammiertes_Sitz_SG',[76,78]],
  ['9x1:TUER_VORNE:Unprogrammiertes_Tuer_Vorne_SG',[30,31]],
  ['9x1:Getriebesteuerung_PDK:PDK_unprogrammiert',2],
  ['9x1:Waehlhebel:WH_unprogrammiert',68],
  ['9x1:Head_Unit:HeadUnit_unprogrammiert',70],
  ['9x1:TVTuner:TVTuner_unprogrammiert',75],
  ['9x1:Verstaerker:ASK_Verstaerker_unprogrammiert',65],
  ['9x1:Verstaerker:BOSE_Verstaerker_unprogrammiert',65],
  ['9x1:CAN_CAN_Gateway:Werksdatensatz_Gateway',9],
  ['9x1:CAN_CAN_Gateway:unprogrammiertes_Gateway',9],
  ['9x1:Steering_Control_Unit:unprogrammierte_EPS',69],
  ['9x1:ACC3:ACC3_unprogrammiert',64],
  ['9x1:Airbag:Unprogrammiertes_Airbag_SG',4],
  ['9x1:BCM_hinten:BCM_hinten_unprogrammiertes_SG',35],
  ['9x1:BCM_vorne:BCM_vorne_uncodiertes_Basis_SG',32],
  ['9x1:BCM_vorne:BCM_vorne_uncodiertes_Max_SG',32],
  ['9x1:BKE:unprogrammierte_BKE',7],
  ['9x1:EPB:unprogrammierte_Parkbremse',50],
  ['9x1:Kombiinstrument_MM:unprogrammiertes_Kombiinstrument_MM',8],
  ['9x1:Lenkstockschalter:Unprogrammiertes_Kombilenkstockmodul_SG',54],
  ['9x1:PASM_PADM:unprogrammiertes_PASM',19],
  ['9x1:PDC:unprogrammiertes_SG_PDC',18],
]);
const faultReadTargets = profileId => FAULT_ONLY_PROFILES.has(profileId)
  ? [].concat(FAULT_ONLY_PROFILES.get(profileId)) : liveReadTargets(profileId);
const faultProfileFitsGeneration = (profileId,generation) => FAULT_ONLY_PROFILES.has(profileId)
  ? ['981','982'].includes(generation) : profileFitsGeneration(profileId,generation);

export { READ_PROFILES, LIVE_READ_PROFILES, liveReadTargets, profileFitsGeneration,
  FAULT_ONLY_PROFILES, faultReadTargets, faultProfileFitsGeneration };
