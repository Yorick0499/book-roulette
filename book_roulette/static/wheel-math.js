/* Angles measured clockwise; segment zero begins at the top pointer. */
(function (root) {
  'use strict';
  const normalize = degrees => ((degrees % 360) + 360) % 360;
  function targetRotation(current, winner, count) {
    if (!Number.isInteger(count) || count < 1 || !Number.isInteger(winner) || winner < 0 || winner >= count) {
      throw new RangeError('Nieprawidłowy segment koła');
    }
    const desired = normalize(-(winner + 0.5) * 360 / count);
    return current + 5 * 360 + normalize(desired - normalize(current));
  }
  function pointerIndex(rotation, count) {
    return Math.floor(normalize(-rotation) / (360 / count)) % count;
  }
  const api = { targetRotation, pointerIndex, normalize };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.WheelMath = api;
}(typeof globalThis !== 'undefined' ? globalThis : window));
