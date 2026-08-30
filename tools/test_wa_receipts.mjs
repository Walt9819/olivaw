// Drive the receipt store exactly as Baileys would, and assert the ack ladder.
//
// The module under test is generated from wa_patch._STORE by test_whatsapp_kit.py,
// so this exercises the JavaScript that is actually injected into Hermes' bridge -
// not a copy of it that can drift.
import assert from 'node:assert/strict';
import {
  olivawReceipts, olivawSeedOutbound, olivawRecordStatus, olivawRecordReceipt, OLIVAW_STATUS,
} from './_wa_store.generated.mjs';

let pass = 0;
function t(name, fn) {
  try { fn(); console.log('  ok   ' + name); pass++; }
  catch (e) { console.log('  FAIL ' + name + '\n       ' + e.message); process.exitCode = 1; }
}
const get = id => olivawReceipts.get(id);

t('a fresh send registers as pending, not as delivered', () => {
  olivawSeedOutbound({ key: { id: 'M1', remoteJid: '5215551234567@s.whatsapp.net' } });
  assert.equal(get('M1').statusName, 'pending');
  assert.equal(get('M1').serverAckAt, null);
  assert.equal(get('M1').deliveredAt, null);
});

t('SERVER_ACK stamps the server time but not delivery', () => {
  olivawRecordStatus({ id: 'M1', fromMe: true }, { status: OLIVAW_STATUS.SERVER_ACK });
  assert.equal(get('M1').statusName, 'server_ack');
  assert.ok(get('M1').serverAckAt);
  assert.equal(get('M1').deliveredAt, null);
});

t('DELIVERY_ACK stamps delivery and backfills the server ack', () => {
  olivawRecordStatus({ id: 'M1', fromMe: true }, { status: OLIVAW_STATUS.DELIVERY_ACK });
  assert.equal(get('M1').statusName, 'delivery_ack');
  assert.ok(get('M1').deliveredAt);
  assert.equal(get('M1').readAt, null);
});

t('a late PENDING cannot walk the state backwards', () => {
  olivawRecordStatus({ id: 'M1', fromMe: true }, { status: OLIVAW_STATUS.READ });
  olivawRecordStatus({ id: 'M1', fromMe: true }, { status: OLIVAW_STATUS.PENDING });
  assert.equal(get('M1').statusName, 'read', 'out-of-order events must not erase progress');
});

t('string status names work as well as the numeric enum', () => {
  olivawSeedOutbound({ key: { id: 'M2', remoteJid: 'x@s.whatsapp.net' } });
  olivawRecordStatus({ id: 'M2', fromMe: true }, { status: 'DELIVERY_ACK' });
  assert.equal(get('M2').statusName, 'delivery_ack');
});

t('inbound messages from other people are not tracked as ours', () => {
  olivawRecordStatus({ id: 'THEIRS', fromMe: false }, { status: OLIVAW_STATUS.READ });
  assert.equal(get('THEIRS'), undefined);
});

t('group receipts promote via timestamps, not status', () => {
  olivawSeedOutbound({ key: { id: 'G1', remoteJid: '123@g.us' } });
  olivawRecordReceipt({ id: 'G1', remoteJid: '123@g.us' }, { receiptTimestamp: 1700000000 });
  assert.equal(get('G1').statusName, 'delivery_ack');
  olivawRecordReceipt({ id: 'G1', remoteJid: '123@g.us' }, { readTimestamp: 1700000001 });
  assert.equal(get('G1').statusName, 'read');
});

t('an ERROR status is recorded as a failure, not silently swallowed', () => {
  olivawSeedOutbound({ key: { id: 'E1', remoteJid: 'y@s.whatsapp.net' } });
  olivawRecordStatus({ id: 'E1', fromMe: true }, { status: OLIVAW_STATUS.ERROR });
  assert.ok(get('E1').error, 'ERROR must leave a readable reason');
});

t('a poll-only update carries no status and changes nothing', () => {
  const before = JSON.stringify(get('M2'));
  olivawRecordStatus({ id: 'M2', fromMe: true }, { pollUpdates: [{}] });
  assert.equal(JSON.stringify(get('M2')), before);
});

t('the store stays bounded under sustained sending', () => {
  const cap = parseInt(process.env.OLIVAW_RECEIPT_MAX || '2000', 10);
  for (let i = 0; i < cap + 250; i++) {
    olivawSeedOutbound({ key: { id: 'BULK' + i, remoteJid: 'z@s.whatsapp.net' } });
  }
  assert.ok(olivawReceipts.size <= cap, `size ${olivawReceipts.size} exceeded cap ${cap}`);
  assert.equal(get('BULK' + (cap + 249)).statusName, 'pending', 'newest must survive eviction');
});

console.log(`\n${pass} passed`);
