export function createBus() {
  const listeners = new Map();
  let health;
  const bus = {
    on(event, fn) {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event).add(fn);
      if (event === "health" && health !== undefined) fn(health);
      return () => listeners.get(event)?.delete(fn);
    },
    emit(event, payload) {
      if (event === "health") health = payload;
      for (const fn of [...(listeners.get(event) || [])]) {
        Promise.resolve().then(() => fn(payload)).catch(error => {
          console.error(error);
          if (event !== "toast") bus.emit("toast", {kind:"error",title:"操作失败",detail:error.message});
        });
      }
    },
  };
  return bus;
}
