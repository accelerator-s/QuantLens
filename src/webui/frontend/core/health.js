
// 不用各自维护轮询。

let current = undefined;
const listeners = new Set();

export function createRestartDetector() {
  let instance, detected = false;
  return status => {
    const next = status?.server?.instance_id;
    if (!next || detected) return false;
    if (instance && instance !== next) { detected = true; return true; }
    instance = next;
    return false;
  };
}

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
