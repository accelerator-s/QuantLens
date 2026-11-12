
// 不用各自维护轮询。

let current = undefined;
const listeners = new Set();

export function initHealth(bus) {
  bus.on("health", (data) => {
    current = data;
    listeners.forEach((fn) => fn(data));
  });
}

export function getHealth() {
  return current;
}

export function workspaceOk() {
  return Boolean(current && current.workspace && current.workspace.missing === 0);
}

export function onHealth(fn) {
  listeners.add(fn);
  if (current !== undefined) fn(current);
  return () => listeners.delete(fn);
}
