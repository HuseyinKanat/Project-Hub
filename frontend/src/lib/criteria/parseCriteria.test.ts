/**
 * parseCriteria.test.ts — PH-301
 *
 * Locks the pure, deterministic AC/TC detector contract. Runs via Node's
 * built-in test runner (node:test + native TS strip), NOT the app tsc — the
 * repo convention (see attachments/grouping.test.ts, identityGuard.test.ts).
 * Kept import-free of aliases so `--experimental-strip-types` needs no resolver.
 *   node --test --experimental-strip-types src/lib/criteria/parseCriteria.test.ts
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { parseCriteria } from "./parseCriteria.ts";
import type { CriteriaCard } from "./parseCriteria.ts";

function one(content: string): CriteriaCard {
  const cards = parseCriteria(content);
  assert.ok(cards && cards.length === 1, "expected exactly one card");
  return cards[0];
}

test("heading GWT acceptance criterion → labelled clause card", () => {
  const card = one(
    [
      "### AC1: Login succeeds",
      "- GIVEN valid credentials",
      "- WHEN the user submits",
      "- THEN the dashboard loads",
    ].join("\n"),
  );
  assert.equal(card.kind, "ac");
  assert.equal(card.id, "AC1");
  assert.equal(card.title, "Login succeeds");
  assert.deepEqual(card.given, ["valid credentials"]);
  assert.deepEqual(card.when, ["the user submits"]);
  assert.deepEqual(card.then, ["the dashboard loads"]);
});

test("AND/BUT clauses extend the preceding bucket", () => {
  const card = one(
    [
      "#### AC2: Multi-condition",
      "- GIVEN a workflow",
      "- AND an exempt type",
      "- WHEN transitioning",
      "- THEN gates are bypassed",
      "- AND no error is raised",
    ].join("\n"),
  );
  assert.deepEqual(card.given, ["a workflow", "an exempt type"]);
  assert.deepEqual(card.when, ["transitioning"]);
  assert.deepEqual(card.then, ["gates are bypassed", "no error is raised"]);
});

test("checklist single-line GWT items → one card each, checkbox preserved", () => {
  const cards = parseCriteria(
    [
      "- [ ] GIVEN a config WHEN transitioning THEN fields are validated",
      "- [x] GIVEN an epic WHEN transitioning THEN gates are bypassed",
    ].join("\n"),
  );
  assert.ok(cards);
  assert.equal(cards.length, 2);
  assert.equal(cards[0].kind, "ac");
  assert.equal(cards[0].id, "AC1");
  assert.equal(cards[0].checked, false);
  assert.deepEqual(cards[0].given, ["a config"]);
  assert.deepEqual(cards[0].then, ["fields are validated"]);
  assert.equal(cards[1].id, "AC2");
  assert.equal(cards[1].checked, true);
  assert.deepEqual(cards[1].when, ["transitioning"]);
});

test("checklist item with explicit AC-id prefix keeps that id", () => {
  const card = one("- [x] AC7: GIVEN x WHEN y THEN z");
  assert.equal(card.id, "AC7");
  assert.equal(card.checked, true);
  assert.deepEqual(card.given, ["x"]);
  assert.deepEqual(card.then, ["z"]);
});

test("plain checklist items without GWT or AC-id are ignored → null", () => {
  assert.equal(
    parseCriteria(["- [ ] buy milk", "- [x] walk the dog"].join("\n")),
    null,
  );
});

test("heading AC block without GWT is still a (plain) AC card", () => {
  const card = one(
    ["### AC3: Cleanup", "- both files deleted", "- grep is clean"].join("\n"),
  );
  assert.equal(card.kind, "ac");
  assert.equal(card.id, "AC3");
  assert.equal(card.title, "Cleanup");
  assert.deepEqual(card.given, []);
  assert.deepEqual(card.then, []);
  assert.match(card.raw, /both files deleted/);
});

test("test case with EN/TR labels → preconditions, numbered steps, expected", () => {
  const card = one(
    [
      "### TC1: Happy path checkout",
      "- Önkoşul: user is logged in",
      "- Adımlar:",
      "  1. add item to cart",
      "  2. click checkout",
      "- Beklenen: order confirmation is shown",
    ].join("\n"),
  );
  assert.equal(card.kind, "tc");
  assert.equal(card.id, "TC1");
  assert.equal(card.title, "Happy path checkout");
  assert.deepEqual(card.preconditions, ["user is logged in"]);
  assert.deepEqual(card.steps, ["add item to cart", "click checkout"]);
  assert.equal(card.expected, "order confirmation is shown");
});

test("test case with English labels (Precondition/Steps/Expected)", () => {
  const card = one(
    [
      "## TC2: API error",
      "- Precondition: token expired",
      "- Steps:",
      "  1. call GET /me",
      "- Expected: 401 is returned",
    ].join("\n"),
  );
  assert.equal(card.id, "TC2");
  assert.deepEqual(card.preconditions, ["token expired"]);
  assert.deepEqual(card.steps, ["call GET /me"]);
  assert.equal(card.expected, "401 is returned");
});

test("mixed AC + TC blocks parse in document order", () => {
  const cards = parseCriteria(
    [
      "### AC1: Renders",
      "- GIVEN a ticket WHEN opened THEN the card shows",
      "",
      "### TC1: Manual check",
      "- Önkoşul: dev server up",
      "- Beklenen: card is visible",
    ].join("\n"),
  );
  assert.ok(cards);
  assert.equal(cards.length, 2);
  assert.deepEqual(
    cards.map((c) => [c.kind, c.id]),
    [
      ["ac", "AC1"],
      ["tc", "TC1"],
    ],
  );
});

test("non-conforming markdown → null (fallback signal)", () => {
  assert.equal(
    parseCriteria("Just a normal description.\n\n- a bullet\n- another"),
    null,
  );
  assert.equal(parseCriteria("## Overview\nSome prose here."), null);
});

test("empty / whitespace / nullish content → null", () => {
  assert.equal(parseCriteria(""), null);
  assert.equal(parseCriteria("   \n  \n"), null);
  assert.equal(parseCriteria(null), null);
  assert.equal(parseCriteria(undefined), null);
});

test("headings like '### Acceptance Criteria' do not false-match (need AC\\d+)", () => {
  assert.equal(parseCriteria("### Acceptance Criteria\nsome text"), null);
});

test("parser is deterministic (same input → identical output)", () => {
  const input = "### AC1: X\n- GIVEN a WHEN b THEN c";
  assert.deepEqual(parseCriteria(input), parseCriteria(input));
});
