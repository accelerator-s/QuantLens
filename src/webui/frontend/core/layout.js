// DOM rectangles and pointer positions use screen pixels; CSS sizes use layout pixels.
export function pageScale() {
  return parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
}
