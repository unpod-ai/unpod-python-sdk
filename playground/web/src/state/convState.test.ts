/**
 * Unit tests for the pure conversation-state → UI mappings.
 *
 * Run with Node's built-in test runner (native TS type-stripping):
 *   node --test src/state/convState.test.ts
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { agentPill, isTurnActive, pipelineFlags, type ConvState } from "./convState.ts";

const ALL: ConvState[] = [
  "idle",
  "listening",
  "thinking",
  "speaking",
  "interrupted",
];

test("pipelineFlags lights the right segment per state", () => {
  assert.deepEqual(pipelineFlags("idle"), {
    sttOn: false,
    dmOn: false,
    ttsOn: false,
  });
  assert.deepEqual(pipelineFlags("listening"), {
    sttOn: true,
    dmOn: false,
    ttsOn: false,
  });
  assert.deepEqual(pipelineFlags("thinking"), {
    sttOn: false,
    dmOn: true,
    ttsOn: false,
  });
  assert.deepEqual(pipelineFlags("speaking"), {
    sttOn: false,
    dmOn: false,
    ttsOn: true,
  });
  assert.deepEqual(pipelineFlags("interrupted"), {
    sttOn: true,
    dmOn: false,
    ttsOn: false,
  });
});

test("at most one pipeline segment is lit for any state", () => {
  for (const s of ALL) {
    const f = pipelineFlags(s);
    const lit = [f.sttOn, f.dmOn, f.ttsOn].filter(Boolean).length;
    assert.ok(lit <= 1, `${s} lit ${lit} segments`);
  }
});

test("agentPill maps every state to its label", () => {
  const expected: Record<ConvState, string> = {
    idle: "Ready",
    listening: "Listening",
    thinking: "Thinking",
    speaking: "Speaking",
    interrupted: "Interrupted",
  };
  for (const s of ALL) {
    assert.equal(agentPill(s), expected[s]);
  }
});

test("isTurnActive is true only while a turn is mid-flight", () => {
  assert.equal(isTurnActive("idle"), false);
  assert.equal(isTurnActive("listening"), false);
  assert.equal(isTurnActive("thinking"), true);
  assert.equal(isTurnActive("speaking"), true);
  assert.equal(isTurnActive("interrupted"), true);
});
