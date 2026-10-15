// Progressive enhancements. Every page works without this file.
(() => {
  // "Use my location" buttons stay hidden unless the browser can do it.
  if ("geolocation" in navigator) {
    document.querySelectorAll("[data-geo]").forEach((button) => {
      button.hidden = false;
      button.addEventListener("click", () => {
        const prefix = button.dataset.geo === "checkin" ? "checkin" : "location";
        button.disabled = true;
        navigator.geolocation.getCurrentPosition(
          (pos) => {
            const form = button.closest("form");
            form.elements[`${prefix}_lat`].value = pos.coords.latitude.toFixed(6);
            form.elements[`${prefix}_lng`].value = pos.coords.longitude.toFixed(6);
            button.disabled = false;
          },
          () => { button.disabled = false; },
          { enableHighAccuracy: true, timeout: 15000 },
        );
      });
    });
  }

  // Show a thumbnail as soon as a photo is chosen.
  document.querySelectorAll('input[type="file"][accept^="image"]').forEach((input) => {
    input.addEventListener("change", () => {
      input.parentElement.querySelector("img")?.remove();
      const file = input.files && input.files[0];
      if (!file) return;
      const img = document.createElement("img");
      img.alt = "";
      img.src = URL.createObjectURL(file);
      input.parentElement.prepend(img);
    });
  });

  // Fill in a bookmark's title from the page it points at.
  const link = document.getElementById("f-bookmark-of");
  const title = document.getElementById("f-name");
  if (link && title) {
    link.addEventListener("change", async () => {
      if (title.value || !link.value) return;
      try {
        const r = await fetch(`/api/preview?url=${encodeURIComponent(link.value)}`);
        const info = await r.json();
        if (info.name && !title.value) title.value = info.name;
      } catch { /* the title is optional */ }
    });
  }

  // Build badge: while the site is building, check back until it is live or has failed.
  const badge = document.querySelector("[data-poll]");
  if (badge) {
    const labels = { pending: "building", live: "live", failed: "build failed" };
    let tries = 0;
    const check = async () => {
      if (++tries > 60 || document.hidden) return schedule();
      try {
        const r = await fetch(badge.dataset.poll, { headers: { Accept: "application/json" } });
        const build = await r.json();
        if (build.state && build.state !== "pending") {
          const text = labels[build.state] || build.state;
          badge.classList.toggle("danger", build.state === "failed");
          badge.replaceChildren();
          if (build.run_url) {
            const a = document.createElement("a");
            a.href = build.run_url;
            a.textContent = text;
            badge.append(a);
          } else {
            badge.textContent = text;
          }
          badge.removeAttribute("data-poll");
          return;
        }
      } catch { /* try again */ }
      schedule();
    };
    const schedule = () => { if (tries <= 60) setTimeout(check, 5000); };
    schedule();
  }

  // Tags: suggest for the tag being typed, not the whole comma separated value.
  const tagInput = document.querySelector("input[data-tags]");
  const options = tagInput && document.querySelectorAll("#known-tags option");
  if (tagInput && options.length) {
    const known = [...options].map((o) => o.value);
    tagInput.removeAttribute("list"); // the browser's own list only completes the whole field
    tagInput.setAttribute("role", "combobox");
    tagInput.setAttribute("aria-expanded", "false");
    tagInput.setAttribute("aria-controls", "tag-suggestions");
    tagInput.setAttribute("aria-autocomplete", "list");
    const menu = document.createElement("ul");
    menu.id = "tag-suggestions";
    menu.className = "suggestions";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;
    tagInput.after(menu);
    let active = -1;

    const current = () => {
      const upto = tagInput.value.slice(0, tagInput.selectionStart ?? tagInput.value.length);
      return upto.slice(upto.lastIndexOf(",") + 1).trim();
    };
    const close = () => {
      menu.hidden = true;
      active = -1;
      tagInput.setAttribute("aria-expanded", "false");
      tagInput.removeAttribute("aria-activedescendant");
    };
    const pick = (tag) => {
      const caret = tagInput.selectionStart ?? tagInput.value.length;
      const start = tagInput.value.lastIndexOf(",", caret - 1) + 1;
      const rest = tagInput.value.slice(caret).replace(/^[^,]*/, "");
      const head = tagInput.value.slice(0, start).replace(/\s*$/, "");
      tagInput.value = `${head}${head ? ", " : ""}${tag}${rest ? "" : ", "}${rest}`;
      close();
      tagInput.focus();
    };
    const show = () => {
      const typed = current().toLowerCase();
      const have = new Set(tagInput.value.split(",").map((t) => t.trim().toLowerCase()));
      const hits = known
        .filter((t) => !have.has(t.toLowerCase()) && (!typed || t.toLowerCase().includes(typed)))
        .sort((a, b) => Number(b.toLowerCase().startsWith(typed)) - Number(a.toLowerCase().startsWith(typed)))
        .slice(0, 8);
      menu.replaceChildren(...hits.map((tag, i) => {
        const li = document.createElement("li");
        li.id = `tag-option-${i}`;
        li.setAttribute("role", "option");
        li.textContent = tag;
        li.addEventListener("mousedown", (e) => { e.preventDefault(); pick(tag); });
        return li;
      }));
      active = -1;
      menu.hidden = hits.length === 0;
      tagInput.setAttribute("aria-expanded", String(hits.length > 0));
    };
    const highlight = (i) => {
      const items = [...menu.children];
      items.forEach((li, n) => li.setAttribute("aria-selected", String(n === i)));
      active = i;
      if (items[i]) tagInput.setAttribute("aria-activedescendant", items[i].id);
    };

    tagInput.addEventListener("input", show);
    tagInput.addEventListener("focus", show);
    tagInput.addEventListener("blur", close);
    tagInput.addEventListener("keydown", (e) => {
      if (menu.hidden) return;
      const count = menu.children.length;
      if (e.key === "ArrowDown") { e.preventDefault(); highlight((active + 1) % count); }
      else if (e.key === "ArrowUp") { e.preventDefault(); highlight((active - 1 + count) % count); }
      else if (e.key === "Enter" && active >= 0) { e.preventDefault(); pick(menu.children[active].textContent); }
      else if (e.key === "Escape") { close(); }
    });
  }
})();
