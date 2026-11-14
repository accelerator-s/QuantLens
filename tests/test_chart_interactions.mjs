import assert from 'node:assert/strict';
import test from 'node:test';
import {createChartViewport} from '../src/webui/frontend/core/chart-viewport.js';
import {lineChart, areaChart} from '../src/webui/frontend/core/charts.js';

const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);
const pause = () => new Promise(resolve => setTimeout(resolve, 190));

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attributes = {}; this.style = {};
    this.dataset = {}; this.listeners = new Map(); this.className = ''; this.hidden = false;
    this.captured = new Set(); this.isConnected = true; this.offsetWidth = 160;
    this.rect = tag === 'svg' ? {left: 0, top: 28, width: 760, height: 200} : {left: 0, top: 0, width: 760, height: 228};
  }
  get classList() {
    const node = this;
    return {
      contains: name => node.className.split(' ').includes(name),
      add: (...names) => { node.className = [...new Set([...node.className.split(' ').filter(Boolean), ...names])].join(' '); },
      remove: (...names) => { node.className = node.className.split(' ').filter(name => !names.includes(name)).join(' '); },
      toggle(name, active) { if (active) this.add(name); else this.remove(name); },
    };
  }
  setAttribute(key, value) { this.attributes[key] = String(value); if (key === 'class') this.className = value; }
  append(...nodes) {
    for (const node of nodes) {
      if (node.parent) node.parent.children = node.parent.children.filter(child => child !== node);
      node.parent = this; this.children.push(node);
    }
  }
  replaceChildren(...nodes) { for (const child of this.children) child.parent = null; this.children = []; this.append(...nodes); }
  addEventListener(name, fn, options) { const listeners = this.listeners.get(name) || []; listeners.push({fn, options}); this.listeners.set(name, listeners); }
  removeEventListener(name, fn) { this.listeners.set(name, (this.listeners.get(name) || []).filter(item => item.fn !== fn)); }
  emit(name, properties = {}) {
    const event = {clientX: 401, clientY: 121, ctrlKey: false, pointerId: 1, pointerType: 'mouse', button: 0,
      deltaY: 0, deltaMode: 0, defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; }, ...properties};
    for (const {fn} of this.listeners.get(name) || []) fn(event);
    return event;
  }
  getBoundingClientRect() { return this.rect; }
  setPointerCapture(id) { this.captured.add(id); }
  hasPointerCapture(id) { return this.captured.has(id); }
  releasePointerCapture(id) { this.captured.delete(id); }
}

const observers = [];
globalThis.document = {
  createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag),
  createTextNode: text => Object.assign(new Element('#text'), {textContent: text}),
};
globalThis.getComputedStyle = () => ({zoom: '1'});
globalThis.window = new Element('window');
globalThis.ResizeObserver = class {
  constructor(callback) { this.callback = callback; observers.push(this); }
  observe(target) { this.target = target; }
  disconnect() { this.disconnected = true; }
  resize(width) { this.target.rect.width = width; this.callback([{contentRect: {width}}]); }
};
function find(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children) { const found = find(child, predicate); if (found) return found; }
}
const byClass = (node, name) => find(node, child => child.classList.contains(name));
const points = chart => byClass(chart, 'chart__line').attributes.points;
const coordinates = chart => points(chart).split(' ').map(point => point.split(',').map(Number));

test('market index gaps split curves including gaps omitted by downsampling', () => {
  const chart = lineChart({labels: ['A', 'B', 'C', 'D', 'E', 'F', 'G'], series: [
    {label: '指数', color: 'green', connectGaps: false, breakBefore: [5], values: [1, 2, null, 4, 5, 6, 7]},
  ]});
  const lines = [];
  function collect(node) {
    if (node.classList.contains('chart__line')) lines.push(node.attributes.points);
    node.children.forEach(collect);
  }
  collect(chart);
  assert.equal(lines.length, 3);
  assert.deepEqual(lines.map(line => line.split(' ').length), [2, 2, 2]);
  chart.dispose();
});
function fixture(interactive = true) {
  return lineChart({interactive, height: 200, labels: Array.from({length: 11}, (_, i) => `D${i}`),
    series: [{label: '回报', color: 'blue', values: Array.from({length: 11}, (_, i) => i)}]});
}

test('zoom stays anchored, pan follows both axes, bounds and reset stay valid', () => {
  const initial = {xMin: 0, xMax: 100, yMin: 0, yMax: 200};
  const viewport = createChartViewport(initial);
  viewport.zoom(.5, .25, .75);
  let d = viewport.domain;
  near(d.xMin + .25 * (d.xMax - d.xMin), 25);
  near(d.yMin + .75 * (d.yMax - d.yMin), 150);
  viewport.pan(.1, .1);
  near(viewport.domain.xMin, d.xMin - 5);
  near(viewport.domain.yMin, d.yMin + 10);
  viewport.pan(10000, -10000);
  assert.equal(viewport.domain.xMin, 0);
  assert.equal(viewport.domain.yMin, -400);
  for (let i = 0; i < 200; i++) viewport.zoom(.1);
  d = viewport.domain;
  near(d.xMax - d.xMin, 100 / 64);
  near(d.yMax - d.yMin, 200 / 64);
  for (let i = 0; i < 200; i++) viewport.zoom(10);
  d = viewport.domain;
  near(d.xMax - d.xMin, 100);
  near(d.yMax - d.yMin, 800);
  viewport.zoom(NaN); viewport.pan(Infinity, NaN);
  assert.deepEqual(viewport.domain, d);
  viewport.reset(); assert.deepEqual(viewport.domain, initial);
});

test('Ctrl wheel zooms the chart, captures drag in both axes, resets and resizes', () => {
  const chart = fixture();
  const svg = find(chart, node => node.tagName === 'svg');
  assert.ok(!byClass(chart, 'chart__reset'));
  assert.ok(!byClass(chart, 'chart__tools'));
  const original = points(chart);
  svg.emit('mouseenter');
  assert.ok(!chart.classList.contains('is-plot-hover'));
  window.emit('keydown', {ctrlKey: true});
  assert.ok(!chart.classList.contains('is-zooming'));
  assert.equal(svg.emit('wheel', {deltaY: -300}).defaultPrevented, false);
  assert.equal(points(chart), original);
  const wheel = svg.emit('wheel', {ctrlKey: true, deltaY: Math.log(.5) / .002});
  assert.ok(wheel.defaultPrevented);
  assert.equal(svg.listeners.get('wheel')[0].options.passive, false);
  assert.notEqual(points(chart), original);
  const before = coordinates(chart);
  svg.emit('pointerdown');
  assert.ok(svg.hasPointerCapture(1));
  assert.ok(!chart.classList.contains('is-panning'));
  svg.emit('pointermove', {clientX: 470, clientY: 137.2});
  assert.ok(chart.classList.contains('is-panning'));
  const after = coordinates(chart);
  near(after[5][0] - before[5][0], 69);
  near(after[5][1] - before[5][1], 16.2);
  svg.emit('pointerup', {clientX: 470, clientY: 137.2});
  assert.ok(!svg.hasPointerCapture(1));
  assert.ok(!chart.classList.contains('is-panning'));
  svg.emit('wheel', {ctrlKey: true, deltaY: 10});
  assert.equal(chart.dataset.zoomDirection, undefined);
  svg.emit('dblclick');
  assert.equal(points(chart), original);
  observers.at(-1).resize(480);
  assert.equal(svg.attributes.viewBox, '0 0 480 200');
  assert.equal(find(chart, node => node.tagName === 'clipPath').children[0].attributes.width, '410');
  assert.equal(svg.emit('wheel', {ctrlKey: true, clientX: 5}).defaultPrevented, false);
  chart.dispose();
  assert.ok(observers.at(-1).disconnected);
  assert.equal(window.listeners.get('keydown').length, 0);
});

test('hover waits for idle, uses zoomed coordinates, and cancels on leave or disposal', async () => {
  const chart = fixture();
  const svg = find(chart, node => node.tagName === 'svg');
  const tip = byClass(chart, 'chart__tip');
  svg.emit('mouseenter');
  svg.emit('mousemove', {clientX: 194});
  assert.equal(tip.hidden, true);
  await pause();
  assert.equal(tip.hidden, false);
  assert.equal(tip.children[0].textContent, 'D2');
  svg.emit('wheel', {ctrlKey: true, deltaY: Math.log(.5) / .002});
  svg.emit('pointerdown');
  svg.emit('pointermove', {clientX: 470, clientY: 137.2});
  svg.emit('pointerup', {clientX: 470, clientY: 137.2});
  svg.emit('mousemove', {clientX: 194});
  await pause();
  assert.equal(tip.children[0].textContent, 'D3');
  assert.equal(tip.style.top, '38px');
  svg.emit('mousemove'); svg.emit('mouseleave');
  await pause();
  assert.equal(tip.hidden, true);
  svg.emit('mouseenter'); svg.emit('mousemove'); chart.dispose();
  await pause();
  assert.equal(tip.hidden, true);
});

test('other charts retain immediate hover and no wheel interception; sparse inputs stay usable', () => {
  const chart = fixture(false);
  const svg = find(chart, node => node.tagName === 'svg');
  svg.emit('mousemove');
  assert.equal(byClass(chart, 'chart__tip').hidden, false);
  assert.equal(svg.emit('wheel', {ctrlKey: true, deltaY: -100}).defaultPrevented, false);
  assert.ok(!byClass(chart, 'chart__reset'));
  for (const values of [[], [1], [null, NaN, Infinity], [0, 0, 0]]) {
    const sparse = lineChart({interactive: true, labels: values.map((_, i) => `D${i}`), series: [{label: '值', values}]});
    assert.ok(find(sparse, node => node.tagName === 'svg').attributes.viewBox);
    sparse.dispose();
  }
  assert.ok(find(areaChart({values: [-.1, -.2], labels: ['a', 'b']}), node => node.tagName === 'polygon'));
});

test('Ctrl without pointer movement updates hover, and window blur cancels a captured drag', async () => {
  const chart = fixture();
  const svg = find(chart, node => node.tagName === 'svg');
  const tip = byClass(chart, 'chart__tip');
  svg.emit('mouseenter');
  await pause();
  assert.equal(tip.hidden, false);
  window.emit('keydown', {ctrlKey: true});
  assert.equal(tip.hidden, true);
  assert.ok(!chart.classList.contains('is-zooming'));
  window.emit('keyup');
  assert.ok(!chart.classList.contains('is-zooming'));
  await pause();
  assert.equal(tip.hidden, false);
  svg.emit('pointerdown');
  window.emit('blur');
  assert.ok(!svg.hasPointerCapture(1));
  assert.ok(!chart.classList.contains('is-panning'));
  assert.ok(!chart.classList.contains('is-plot-hover'));
  assert.ok(!chart.classList.contains('is-zooming'));
  await pause();
  assert.equal(tip.hidden, true);
  chart.dispose();
});

test('date ticks do not overlap on narrow charts or after a deep zoom', () => {
  const labels = Array.from({length: 100}, (_, i) => `2026-${String(Math.floor(i / 28) + 1).padStart(2, '0')}-${String(i % 28 + 1).padStart(2, '0')}`);
  const chart = lineChart({interactive: true, labels, series: [{label: '回报', color: 'blue', values: labels.map((_, i) => i)}]});
  const svg = find(chart, node => node.tagName === 'svg');
  const observer = observers.at(-1);
  function verify() {
    const ticks = [];
    function visit(node) {
      if (node.classList.contains('chart__tick--x')) ticks.push(node);
      node.children.forEach(visit);
    }
    visit(svg);
    assert.ok(ticks.length >= 1);
    let previous = -Infinity;
    for (const tick of ticks) {
      const size = tick.textContent.length * 6.2 + 4;
      const anchor = tick.attributes['text-anchor'];
      const left = Number(tick.attributes.x) - (anchor === 'end' ? size : anchor === 'middle' ? size / 2 : 0);
      assert.ok(left >= previous + 15.9, `date labels overlap at ${svg.rect.width}px`);
      previous = left + size;
    }
    return ticks.length;
  }
  observer.resize(760);
  const wideCount = verify();
  for (const width of [480, 320, 240, 130]) {
    observer.resize(width);
    assert.ok(verify() <= wideCount);
    const clientX = 56 + (width - 70) / 2;
    svg.emit('wheel', {ctrlKey: true, deltaY: -1000, clientX});
    verify();
    svg.emit('wheel', {ctrlKey: true, deltaY: -1000, clientX});
    verify();
    svg.emit('dblclick');
  }
  chart.dispose();
});

test('a stationary pointer restores the default cursor even while the button is held', async () => {
  const chart = fixture();
  const svg = find(chart, node => node.tagName === 'svg');
  svg.emit('mouseenter');
  assert.ok(!chart.classList.contains('is-panning'));
  svg.emit('pointerdown');
  assert.ok(!chart.classList.contains('is-panning'));
  svg.emit('pointermove', {clientX: 430});
  assert.ok(chart.classList.contains('is-panning'));
  await pause();
  assert.ok(!chart.classList.contains('is-panning'));
  assert.equal(byClass(chart, 'chart__tip').hidden, true);
  svg.emit('pointermove', {clientX: 450});
  assert.ok(chart.classList.contains('is-panning'));
  svg.emit('pointerup', {clientX: 450});
  assert.ok(!chart.classList.contains('is-panning'));
  await pause();
  assert.equal(byClass(chart, 'chart__tip').hidden, false);
  chart.dispose();
});
