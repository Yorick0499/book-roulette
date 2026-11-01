const assert = require('node:assert/strict');
const { targetRotation, pointerIndex } = require('../book_roulette/static/wheel-math.js');
let tested = 0;
for (const count of [1, 2, 10, 100, 1000, 50000]) {
  for (const current of [0, 30, 359, 1800, 7332.19]) {
    for (let winner = 0; winner < count; winner++) {
      const target = targetRotation(current, winner, count);
      assert.equal(pointerIndex(target, count), winner, `count=${count} winner=${winner}`);
      assert.ok(target >= current + 1800);
      tested++;
    }
  }
}
assert.throws(() => targetRotation(0, 0, 0), RangeError);
assert.throws(() => targetRotation(0, 2, 2), RangeError);
console.log(`Wheel geometry: ${tested} winner/pointer checks passed.`);
