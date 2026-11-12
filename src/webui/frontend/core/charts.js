



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
      if (v == null || Number.isNaN(v)) continue;
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


export function lineChart({
  series = [],
  labels = [],
  height = 260,
  yFmt = (v) => v.toFixed(3),
  fillFirst = false,
} = {}) {
  const width = 760;
  const padL = 56;
  const padR = 14;
  const padT = 12;
  const padB = 26;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;
  const n = labels.length;
  const [yMin, yMax] = extent(series);

  const xAt = (i) => padL + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const yAt = (v) => padT + (1 - (v - yMin) / (yMax - yMin)) * plotH;

  const wrap = document.createElement("div");
  wrap.className = "chart";
  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    class: "chart__svg",
    role: "img",
  });
  wrap.append(svg);

  // 网格与 y 轴刻度
  for (const tick of niceTicks(yMin, yMax)) {
    const y = yAt(tick);
    svg.append(
      svgEl("line", { x1: padL, x2: width - padR, y1: y, y2: y, class: "chart__grid" }),
    );
    const label = svgEl("text", { x: padL - 8, y: y + 4, class: "chart__tick chart__tick--y" });
    label.textContent = yFmt(tick);
    svg.append(label);
  }

  
  const xTickCount = Math.min(6, n);
  for (let t = 0; t < xTickCount; t++) {
    const i = xTickCount <= 1 ? 0 : Math.round((t / (xTickCount - 1)) * (n - 1));
    const label = svgEl("text", {
      x: xAt(i),
      y: height - 8,
      class: "chart__tick chart__tick--x",
      "text-anchor": t === 0 ? "start" : t === xTickCount - 1 ? "end" : "middle",
    });
    label.textContent = labels[i] ?? "";
    svg.append(label);
  }

  // 序列
  series.forEach((s, idx) => {
    const pts = [];
    s.values.forEach((v, i) => {
      if (v == null || Number.isNaN(v)) return;
      pts.push(`${xAt(i).toFixed(2)},${yAt(v).toFixed(2)}`);
    });
    if (!pts.length) return;
    if (fillFirst && idx === 0) {
      const first = pts[0].split(",")[0];
      const last = pts[pts.length - 1].split(",")[0];
      const base = yAt(Math.max(yMin, Math.min(0, yMax))).toFixed(2);
      const area = svgEl("polygon", {
        points: `${first},${base} ${pts.join(" ")} ${last},${base}`,
        class: "chart__area",
        style: `fill: ${s.color}; opacity: 0.18;`,
      });
      svg.append(area);
    }
    svg.append(
      svgEl("polyline", {
        points: pts.join(" "),
        class: "chart__line",
        style: `stroke: ${s.color};`,
      }),
    );
  });

  // hover 读数
  const cursor = svgEl("line", { y1: padT, y2: padT + plotH, class: "chart__cursor" });
  cursor.style.display = "none";
  svg.append(cursor);
  const tip = document.createElement("div");
  tip.className = "chart__tip";
  tip.hidden = true;
  wrap.append(tip);

  svg.addEventListener("mousemove", (ev) => {
    if (!n) return;
    const rect = svg.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * width;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - padL) / plotW) * (n - 1))));
    const x = xAt(i);
    cursor.setAttribute("x1", x);
    cursor.setAttribute("x2", x);
    cursor.style.display = "";
    const rows = series
      .filter((s) => s.values[i] != null && !Number.isNaN(s.values[i]))
      .map(
        (s) =>
          `<span class="chart__tip-row"><i style="background:${s.color}"></i>${s.label}：${yFmt(
            s.values[i],
          )}</span>`,
      );
    tip.innerHTML = `<strong>${labels[i] ?? ""}</strong>${rows.join("")}`;
    tip.hidden = false;
    const rel = ev.clientX - rect.left;
    tip.style.left = `${Math.min(rel + 14, rect.width - tip.offsetWidth - 6)}px`;
  });
  svg.addEventListener("mouseleave", () => {
    cursor.style.display = "none";
    tip.hidden = true;
  });

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
