import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const {sameOrigin, audioOnly} = createRequire(import.meta.url)('../desktop/policy.cjs');

test('desktop allows only its exact runtime origin', () => {
  const origin = 'http://127.0.0.1:8420';
  assert.equal(sameOrigin(`${origin}/api/status`, origin), true);
  for (const url of ['http://127.0.0.1:8421', 'http://localhost:8420', 'https://example.com', 'file:///etc/passwd', 'javascript:alert(1)', 'broken']) {
    assert.equal(sameOrigin(url, origin), false);
  }
});

test('desktop permission policy excludes camera, screen and unspecified media', () => {
  assert.equal(audioOnly('media', {mediaTypes: ['audio']}), true);
  assert.equal(audioOnly('media', {mediaType: 'audio'}), true);
  for (const details of [{}, {mediaTypes: []}, {mediaTypes: ['audio', 'video']}, {mediaType: 'video'}]) {
    assert.equal(audioOnly('media', details), false);
  }
  assert.equal(audioOnly('display-capture', {mediaTypes: ['audio']}), false);
});
