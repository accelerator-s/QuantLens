


const cache = new Map();

async function rawSvg(name) {
  if (!cache.has(name)) {
    cache.set(
      name,
      fetch(`/resources/icons/${name}.svg`)
        .then((r) => (r.ok ? r.text() : ""))
        .catch(() => "")
    );
  }
  return cache.get(name);
}

export async function iconEl(name) {
  const span = document.createElement("span");
  span.className = "icon";
  span.innerHTML = await rawSvg(name);
  return span;
}


export async function hydrateIcons(root) {
  const targets = root.querySelectorAll("[data-icon]");
  await Promise.all(
    [...targets].map(async (el) => {
      el.classList.add("icon");
      el.innerHTML = await rawSvg(el.dataset.icon);
    })
  );
}

export const icons = { iconEl, hydrateIcons };

export const loadIcon = rawSvg;
