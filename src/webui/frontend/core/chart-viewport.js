const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

function fit(min, span, lower, upper) {
  const start = clamp(min, lower, upper - span);
  return [start, start + span];
}

export function createChartViewport(initial) {
  const xSpan = initial.xMax - initial.xMin;
  const ySpan = initial.yMax - initial.yMin;
  let domain = {...initial};
  const yLower = initial.yMin - ySpan * 2;
  const yUpper = initial.yMax + ySpan * 2;

  return {
    get domain() { return {...domain}; },
    reset() { domain = {...initial}; },
    zoom(factor, anchorX = .5, anchorY = .5) {
      if (!Number.isFinite(factor) || factor <= 0) return;
      const x = clamp(anchorX, 0, 1);
      const y = clamp(anchorY, 0, 1);
      const oldX = domain.xMax - domain.xMin;
      const oldY = domain.yMax - domain.yMin;
      const nextX = clamp(oldX * factor, xSpan / 64, xSpan);
      const nextY = clamp(oldY * factor, ySpan / 64, ySpan * 4);
      const [xMin, xMax] = fit(domain.xMin + x * (oldX - nextX), nextX, initial.xMin, initial.xMax);
      const [yMin, yMax] = fit(domain.yMin + y * (oldY - nextY), nextY, yLower, yUpper);
      domain = {xMin, xMax, yMin, yMax};
    },
    // Fractions of the plot's screen width / height; the data follows the pointer.
    pan(dx, dy) {
      if (!Number.isFinite(dx) || !Number.isFinite(dy)) return;
      const x = domain.xMax - domain.xMin;
      const y = domain.yMax - domain.yMin;
      const [xMin, xMax] = fit(domain.xMin - dx * x, x, initial.xMin, initial.xMax);
      const [yMin, yMax] = fit(domain.yMin + dy * y, y, yLower, yUpper);
      domain = {xMin, xMax, yMin, yMax};
    },
  };
}
