import {createChartViewport} from "./chart-viewport.js";
const NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  return el;
}

function extent(seriesList) {
  let min = Infinity;
  let max = -Infinity;
  for (const s of seriesList) {
    for (const v of s.values) {
      if (v == null || !Number.isFinite(v)) continue;
      if (v < min) min = v;
      if (v > max) max = v;
    }
  }
  if (min === Infinity) return [0, 1];
  if (min === max) return [min - 1, max + 1];
  const pad = (max - min) * 0.06;
  return [min - pad, max + pad];
}

function niceTicks(min, max, count = 4) {
  const span = max - min;
  const step = Math.pow(10, Math.floor(Math.log10(span / count)));
  const err = (span / count) / step;
  const mult = err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
  const tick = step * mult;
  const start = Math.ceil(min / tick) * tick;
  const ticks = [];
  for (let v = start; v <= max + tick * 1e-6; v += tick) ticks.push(v);
  return ticks;
}


let chartId = 0;

export function lineChart({
  series = [], labels = [], height = 260,
  yFmt = (v) => v.toFixed(3), fillFirst = false, interactive = false,
} = {}) {
  let width = 760;
  const padL = 56, padR = 14, padT = 12, padB = 26;
  const plotH = height - padT - padB;
  const n = labels.length;
  const [yMin, yMax] = extent(series);
  const initialDomain = {xMin: 0, xMax: Math.max(1, n - 1), yMin, yMax};
  const viewport = createChartViewport(initialDomain);
  const clipId = `chart-plot-${++chartId}`;
  const wrap = document.createElement("div");
  wrap.className = interactive ? "chart chart--interactive" : "chart";
  const svg = svgEl("svg", {
    class: "chart__svg", role: "img",
    "aria-label": series.map(s => s.label).join("、") || "折线图",
  });
  const tip = document.createElement("div");
  tip.className = "chart__tip";
  tip.hidden = true;
  const cursor = svgEl("line", {y1: padT, y2: padT + plotH, class: "chart__cursor"});
  let drag = null;
  let panCursorTimer;
  let hoverTimer;
  let lastPointer;
  const plotW = () => width - padL - padR;
  const xAt = i => {
    const domain = viewport.domain;
    return n <= 1 ? padL + plotW() / 2 : padL + (i - domain.xMin) / (domain.xMax - domain.xMin) * plotW();
  };
  const yAt = value => {
    const domain = viewport.domain;
    return padT + (domain.yMax - value) / (domain.yMax - domain.yMin) * plotH;
  };
  function hideTip() {
    clearTimeout(hoverTimer);
    cursor.style.display = "none";
    tip.hidden = true;
  }
  function render() {
    hideTip();
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const defs = svgEl("defs");
    const clip = svgEl("clipPath", {id: clipId});
    clip.append(svgEl("rect", {x: padL, y: padT, width: plotW(), height: plotH}));
    defs.append(clip);
    const plot = svgEl("g", {"clip-path": `url(#${clipId})`});
    const axes = svgEl("g");
    const domain = viewport.domain;
    for (const tick of niceTicks(domain.yMin, domain.yMax)) {
      const y = yAt(tick);
      plot.append(svgEl("line", {x1: padL, x2: width - padR, y1: y, y2: y, class: tick === 0 ? "chart__grid chart__zero" : "chart__grid"}));
      const label = svgEl("text", {x: padL - 8, y: y + 4, class: "chart__tick chart__tick--y"});
      label.textContent = yFmt(tick);
      axes.append(label);
    }
    const first = Math.max(0, Math.ceil(domain.xMin));
    const last = Math.min(n - 1, Math.floor(domain.xMax));
    const displayScale = (svg.getBoundingClientRect().width || width) / width;
    const labelWidth = i => String(labels[i] ?? '').length * 6.2 + 4;
    const widest = Math.max(1, ...labels.slice(first, last + 1).map((_, offset) => labelWidth(first + offset)));
    const tickCount = Math.max(1, Math.min(6, last - first + 1, Math.floor(plotW() * displayScale / (widest + 20))));
    const ticks = [];
    for (let t = 0; t < tickCount && last >= first; t++) {
      const i = tickCount <= 1 ? first : Math.round(first + t / (tickCount - 1) * (last - first));
      const anchor = t === 0 ? 'start' : t === tickCount - 1 ? 'end' : 'middle';
      const size = labelWidth(i);
      const x = xAt(i) * displayScale;
      const left = x - (anchor === 'end' ? size : anchor === 'middle' ? size / 2 : 0);
      const right = left + size;
      if (t === tickCount - 1 && tickCount > 1) {
        while (ticks.length && left < ticks.at(-1).right + 16) ticks.pop();
      }
      if (!ticks.length || left >= ticks.at(-1).right + 16) ticks.push({i, anchor, right});
    }
    for (const {i, anchor} of ticks) {
      const label = svgEl('text', {
        x: xAt(i), y: height - 8, class: 'chart__tick chart__tick--x', 'text-anchor': anchor,
      });
      label.textContent = labels[i] ?? '';
      axes.append(label);
    }
    series.forEach((s, idx) => {
      const points = [];
      s.values.forEach((value, i) => {
        if (value == null || !Number.isFinite(value)) return;
        points.push(`${xAt(i).toFixed(2)},${yAt(value).toFixed(2)}`);
      });
      if (!points.length) return;
      if (fillFirst && idx === 0) {
        const start = points[0].split(",")[0];
        const end = points[points.length - 1].split(",")[0];
        const base = yAt(Math.max(yMin, Math.min(0, yMax))).toFixed(2);
        plot.append(svgEl("polygon", {
          points: `${start},${base} ${points.join(" ")} ${end},${base}`,
          class: "chart__area", style: `fill: ${s.color}; opacity: 0.18;`,
        }));
      }
      plot.append(svgEl("polyline", {points: points.join(" "), class: "chart__line", style: `stroke: ${s.color};`}));
    });
    plot.append(cursor);
    svg.replaceChildren(defs, plot, axes);

  }
  function point(event) {
    const rect = svg.getBoundingClientRect();
    return {x: (event.clientX - rect.left) / rect.width * width,
            y: (event.clientY - rect.top) / rect.height * height, rect};
  }
  const inside = p => p.x >= padL && p.x <= width - padR && p.y >= padT && p.y <= padT + plotH;
  function hover(event) {
    if (!n || drag) return;
    const p = point(event);
    if (!inside(p)) { hideTip(); return; }
    const domain = viewport.domain;
    const i = Math.max(0, Math.min(n - 1, Math.round(domain.xMin + (p.x - padL) / plotW() * (domain.xMax - domain.xMin))));
    cursor.setAttribute("x1", xAt(i));
    cursor.setAttribute("x2", xAt(i));
    cursor.style.display = "";
    const title = document.createElement("strong");
    title.textContent = labels[i] ?? "";
    tip.replaceChildren(title);
    for (const s of series) {
      if (s.values[i] == null || !Number.isFinite(s.values[i])) continue;
      const row = document.createElement("span");
      row.className = "chart__tip-row";
      const marker = document.createElement("i");
      marker.style.background = s.color;
      row.append(marker, document.createTextNode(`${s.label}：${yFmt(s.values[i])}`));
      tip.append(row);
    }
    tip.hidden = false;
    tip.style.top = `${p.rect.top - wrap.getBoundingClientRect().top + 10}px`;
    tip.style.left = `${Math.max(0, Math.min(event.clientX - p.rect.left + 14, p.rect.width - tip.offsetWidth - 6))}px`;
  }
  function scheduleHover(event) {
    if (!interactive) { hover(event); return; }
    hideTip();
    if (drag || event.ctrlKey || !inside(point(event))) return;
    const position = {clientX: event.clientX, clientY: event.clientY};
    hoverTimer = setTimeout(() => hover(position), 160);
  }
  svg.addEventListener("mousemove", event => {
    lastPointer = {clientX: event.clientX, clientY: event.clientY};
    scheduleHover(event);
  });
  svg.addEventListener("mouseleave", hideTip);
  if (interactive) {
    const reset = () => { viewport.reset(); render(); };
    svg.style.height = `${height}px`;
    svg.setAttribute("preserveAspectRatio", "none");
    let pointerInside = false;
    const cancelDrag = () => {
      clearTimeout(panCursorTimer);
      const id = drag?.id;
      drag = null;
      wrap.classList.remove("is-panning");
      if (id != null && svg.hasPointerCapture(id)) svg.releasePointerCapture(id);
      window.removeEventListener("blur", cancelDrag);
    };
    const keyChange = event => {
      if (lastPointer && pointerInside) scheduleHover({...lastPointer, ctrlKey: event.ctrlKey});
    };
    const removeKeys = () => {
      window.removeEventListener("keydown", keyChange);
      window.removeEventListener("keyup", keyChange);
      window.removeEventListener("blur", leave);
    };
    const leave = () => {
      pointerInside = false;
      hideTip();
      removeKeys();
    };
    svg.addEventListener("mouseenter", event => {
      pointerInside = true;
      lastPointer = {clientX: event.clientX, clientY: event.clientY};
      window.addEventListener("keydown", keyChange);
      window.addEventListener("keyup", keyChange);
      window.addEventListener("blur", leave);
      scheduleHover(event);
    });
    svg.addEventListener("mouseleave", leave);
    svg.addEventListener("wheel", event => {
      if (!event.ctrlKey || n < 2) return;
      const p = point(event);
      if (!inside(p)) return;
      event.preventDefault();
      lastPointer = {clientX: event.clientX, clientY: event.clientY};
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? height : 1);
      viewport.zoom(Math.exp(Math.max(-1, Math.min(1, delta * .002))), (p.x - padL) / plotW(), 1 - (p.y - padT) / plotH);
      render();
    }, {passive: false});
    svg.addEventListener("pointerdown", event => {
      if (event.button !== 0 || event.pointerType === "touch" || n < 2 || !inside(point(event))) return;
      event.preventDefault();
      drag = {id: event.pointerId, ...point(event)};
      svg.setPointerCapture(event.pointerId);
      window.addEventListener("blur", cancelDrag);
      hideTip();
    });
    svg.addEventListener("pointermove", event => {
      if (!drag || event.pointerId !== drag.id) return;
      const p = point(event);
      if (p.x === drag.x && p.y === drag.y) return;
      wrap.classList.add("is-panning");
      clearTimeout(panCursorTimer);
      panCursorTimer = setTimeout(() => wrap.classList.remove("is-panning"), 160);
      viewport.pan((p.x - drag.x) / plotW(), (p.y - drag.y) / plotH);
      drag.x = p.x; drag.y = p.y;
      render();
    });
    const endDrag = event => {
      if (!drag || event.pointerId !== drag.id) return;
      cancelDrag();
      lastPointer = {clientX: event.clientX, clientY: event.clientY};
      scheduleHover(event);
    };
    for (const name of ["pointerup", "pointercancel", "lostpointercapture"]) svg.addEventListener(name, endDrag);
    svg.addEventListener("dblclick", reset);
    let observer;
    wrap.dispose = () => {
      cancelDrag();
      leave();
      observer?.disconnect();
    };
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(entries => {
        if (!svg.isConnected) { wrap.dispose(); return; }
        const nextWidth = entries[0].contentRect.width;
        if (nextWidth > padL + padR && Math.abs(nextWidth - width) > .5) {
          width = nextWidth;
          render();
        }
      });
      observer.observe(svg);
    }
  }
  wrap.append(svg, tip);
  render();
  return wrap;
}


export function areaChart({ values = [], labels = [], height = 140, yFmt } = {}) {
  return lineChart({
    series: [{ label: "回撤", color: "var(--down)", values }],
    labels,
    height,
    yFmt: yFmt || ((v) => `${(v * 100).toFixed(1)}%`),
    fillFirst: true,
  });
}


export function barChart({ items = [], valueFmt = (v) => String(v) } = {}) {
  const wrap = document.createElement("div");
  wrap.className = "chart-bars";
  const max = Math.max(...items.map((it) => Math.abs(it.value)), 1e-9);
  for (const it of items) {
    const row = document.createElement("div");
    row.className = "chart-bars__row";
    const label = document.createElement("span");
    label.className = "chart-bars__label";
    label.textContent = it.label;
    label.title = it.label;
    const track = document.createElement("span");
    track.className = "chart-bars__track";
    const fill = document.createElement("span");
    fill.style.width = `${(Math.abs(it.value) / max) * 100}%`;
    if (it.color) fill.style.background = it.color;
    track.append(fill);
    const count = document.createElement("span");
    count.className = "chart-bars__value";
    count.textContent = valueFmt(it.value);
    row.append(label, track, count);
    wrap.append(row);
  }
  return wrap;
}

export function candleChart(rows) {
  const wrap=document.createElement('div');wrap.className='chart';
  const svg=svgEl('svg',{viewBox:'0 0 900 360',class:'chart__svg',role:'img','aria-label':'最近120个交易日K线和成交量'});
  const lo=Math.min(...rows.map(r=>r.low)),hi=Math.max(...rows.map(r=>r.high));
  const maxVol=Math.max(...rows.map(r=>r.volume),1),step=820/rows.length;
  const y=v=>24+(hi-v)/Math.max(hi-lo,0.01)*230;
  for(let i=0;i<5;i++){
    const value=lo+(hi-lo)*i/4,level=y(value);
    svg.append(svgEl('line',{x1:58,x2:885,y1:level,y2:level,stroke:'var(--border)'}));
    const label=svgEl('text',{x:50,y:level+4,'text-anchor':'end',fill:'var(--ink-2)','font-size':11});label.textContent=value.toFixed(2);svg.append(label);
  }
  rows.forEach((r,i)=>{
    const x=60+(i+0.5)*step,c=r.close>=r.open?'#cf4c4c':'#168365',group=svgEl('g');
    const title=svgEl('title');title.textContent=`${String(r.date).slice(0,10)} 开 ${r.open} 高 ${r.high} 低 ${r.low} 收 ${r.close} 量 ${r.volume}`;group.append(title);
    group.append(svgEl('line',{x1:x,x2:x,y1:y(r.high),y2:y(r.low),stroke:c}));
    group.append(svgEl('rect',{x:x-step*.3,y:Math.min(y(r.open),y(r.close)),width:Math.max(1,step*.6),height:Math.max(1,Math.abs(y(r.open)-y(r.close))),fill:c}));
    group.append(svgEl('rect',{x:x-step*.3,y:330-r.volume/maxVol*55,width:Math.max(1,step*.6),height:r.volume/maxVol*55,fill:c,opacity:0.6}));svg.append(group);
    if(i%30===0){const label=svgEl('text',{x,y:350,'text-anchor':'middle',fill:'var(--ink-2)','font-size':11});label.textContent=String(r.date).slice(0,10);svg.append(label);}
  });
  wrap.append(svg);return wrap;
}
