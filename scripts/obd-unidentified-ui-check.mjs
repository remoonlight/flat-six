/** Shared Mac-model/Windows-kit checks. No identity or vehicle read is started. */
import assert from 'node:assert/strict';

export async function verifyUnidentifiedLiveUi(page){
  await page.locator('nav.side [data-tab="obd"]').click();
  await page.locator('[data-obd-tab="live"]').click();
  const selection=page.getByTestId('eng-system'),prior=await selection.inputValue();
  for(const ecu of ['dme','gateway']){
    await selection.selectOption(ecu);await page.getByTestId('offline-identity-state').waitFor();
    for(const id of ['eng-data-source','offline-variant','offline-parameters','manufacturer-x431-inactive','manufacturer-readonly','manufacturer-start','obd-recording-controls'])
      assert.equal(await page.getByTestId(id).count(),0,id);
    assert.equal(await page.getByTestId('manufacturer-read').isDisabled(),true);
    assert.equal(await page.getByTestId('manufacturer-save').isDisabled(),true);
  }
  await selection.selectOption(prior);
}

export async function verifyUnreadCodingUi(page){
  await page.locator('[data-obd-tab="coding"]').click();
  await page.locator('[data-coding-system="dme"]').click();
  await page.getByTestId('coding-read-original').waitFor();
  assert.equal(await page.getByTestId('coding-backup-original').count(),1);
  for(const id of ['coding-read-scope-result','diagnostic-preparation','coding-live-fields','coding-write-review'])
    assert.equal(await page.getByTestId(id).count(),0,id);
  for(const name of ['原码范围版本','码值备份','码值块','设码字段'])assert.equal(await page.getByLabel(name,{exact:true}).count(),0,name);
  await page.locator('[data-obd-tab="live"]').click();
}
