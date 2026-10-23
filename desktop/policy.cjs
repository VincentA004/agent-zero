'use strict';
function sameOrigin(url, origin) {
  try { return new URL(url).origin === origin; } catch { return false; }
}
function audioOnly(permission, details = {}) {
  return permission === 'media' && (details.mediaTypes?.length > 0
    ? details.mediaTypes.every(type => type === 'audio') : details.mediaType === 'audio');
}
module.exports = {sameOrigin, audioOnly};
