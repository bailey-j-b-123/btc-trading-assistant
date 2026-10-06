/** el() text safety: a DOM node must never stringify into visible text. */

import test from "node:test";
import assert from "node:assert/strict";

import { el } from "../../src/trading_assistant/web/static/js/util.js";

function fakeElement(tag) {
  return {
    tag,
    children: [],
    attributes: {},
    dataset: {},
    style: {},
    textContent: "",
    className: "",
    listeners: {},
    setAttribute(key, value) { this.attributes[key] = value; },
    addEventListener(type, listener) { this.listeners[type] = listener; },
    append(child) { this.children.push(child); return child; },
  };
}

function withFakeDocument(run) {
  const saved = Object.hasOwn(globalThis, "document") ? globalThis.document : undefined;
  globalThis.document = {
    createElement: (tag) => fakeElement(tag),
    createTextNode: (text) => ({ textNode: text }),
  };
  try {
    return run();
  } finally {
    if (saved === undefined) delete globalThis.document;
    else globalThis.document = saved;
  }
}

/** A node from another document: instanceof Node is false, nodeType is 1. */
const foreignDiv = () => ({ nodeType: 1, nodeName: "DIV" });

test("a node passed as text throws instead of rendering [object HTMLDivElement]", () => {
  withFakeDocument(() => {
    assert.throws(() => el("div", { text: foreignDiv() }), /pass it as a child instead/);
  });
});

test("cross-realm nodes are appended as children, never stringified", () => {
  withFakeDocument(() => {
    const foreign = foreignDiv();
    const node = el("div", {}, [foreign, "tail"]);
    assert.equal(node.children[0], foreign);
    assert.equal(node.children[1].textNode, "tail");
  });
});

test("ordinary strings still render as text", () => {
  withFakeDocument(() => {
    const node = el("div", { text: "AI explains facts" });
    assert.equal(node.textContent, "AI explains facts");
    const mixed = el("div", {}, ["a", null, undefined, 42]);
    assert.deepEqual(mixed.children.map((child) => child.textNode), ["a", "42"]);
  });
});
