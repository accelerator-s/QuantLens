import { hydrateIcons } from "../../core/icons.js";

export async function mountRail(slot, bus, navigate) {
  const html = await fetch("/components/rail/rail.html").then((r) => r.text());
  slot.innerHTML = html;
  await hydrateIcons(slot);

  const links = Array.from(slot.querySelectorAll("[data-route]"));
  links.forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      document.body.classList.remove("rail-open");
      navigate(a.dataset.route);
    });
  });

  bus.on("route", (active) => {
    links.forEach((a) => {
      if (a.dataset.route === active) {
        a.setAttribute("aria-current", "page");
      } else {
        a.removeAttribute("aria-current");
      }
    });
  });

}
