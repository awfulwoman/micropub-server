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
})();
