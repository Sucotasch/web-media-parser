// P4-2 tests: sieve_updater.js pure functions (mirror + validation + merge).
// Run: node extension/tests/sieve_merge.test.mjs

import { jsDelivrMirror, validRuleCount, mergeSieve } from "../sieve_updater.js";

let failures = 0;
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failures++;
    console.error(`FAIL: ${name}\n  expected: ${JSON.stringify(expected)}\n  actual:   ${JSON.stringify(actual)}`);
  } else {
    console.log(`ok: ${name}`);
  }
}

// jsDelivrMirror
check(
  "raw.githubusercontent converts to jsDelivr",
  jsDelivrMirror("https://raw.githubusercontent.com/user/repo/master/data/sieve.json"),
  "https://cdn.jsdelivr.net/gh/user/repo@master/data/sieve.json"
);
check("non-github URL returns null", jsDelivrMirror("https://example.com/x.json"), null);
check("empty URL returns null", jsDelivrMirror(""), null);

// validRuleCount
check(
  "counts rules with link or img",
  validRuleCount({ a: { link: "x" }, b: { img: "y" }, c: {}, d: { other: 1 } }),
  2
);
check("empty sieve counts 0", validRuleCount({}), 0);

// mergeSieve — port of Mod's semantics
const oldSieve = {
  _userCustom: { link: "user-rule", to: "img" },   // user-defined → survives
  Keep: { link: "keep", to: "img", off: 1 },        // off flag carries over
  Gone: { link: "gone", to: "img" },                // removed upstream → off:1
  Changed: { link: "old", to: "img", off: 1 },      // upstream disabled it → stays off
};
const newSieve = {
  Keep: { link: "keep-new", to: "img" },
  Fresh: { link: "fresh", to: "img" },
  Changed: { link: "changed-new", to: "img" },
};
const merged = mergeSieve(oldSieve, newSieve);

check("_-prefixed user rule survives", !!merged._userCustom, true);
check("off flag carried over to updated rule", merged.Keep.off, 1);
check("removed rule kept with off:1", merged.Gone && merged.Gone.off, 1);
check("upstream change accepted", merged.Changed.link, "changed-new");
check("upstream off preserved", merged.Changed.off, 1);
check("new upstream rule added", merged.Fresh.link, "fresh");
check("user rule not present upstream still there", merged._userCustom.link, "user-rule");

if (failures > 0) {
  console.error(`${failures} failure(s)`);
  process.exit(1);
}
console.log("ALL PASS");
