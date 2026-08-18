/*
 * One shared Author Preview / Hover Card component, used everywhere an
 * author byline appears (news, opinion, Arı, homepage, archives...). Author
 * preview data is embedded once per page load (see base.html) so hovering
 * never triggers a network request -- just a lookup in memory.
 */
(function () {
  "use strict";

  const dataEl = document.getElementById("author-preview-data");
  if (!dataEl) return;

  let authors = {};
  try {
    const list = JSON.parse(dataEl.textContent || "[]");
    list.forEach((a) => { authors[a.id] = a; });
  } catch (e) {
    return;
  }

  const card = document.createElement("a");
  card.className = "author-card";
  card.tabIndex = -1; // reachable by mouse click / programmatic focus, but not part of
                       // the natural Tab sequence -- keyboard users already have a direct
                       // path to the profile via the byline link itself.
  card.hidden = true;
  document.body.appendChild(card);

  let activeLink = null;
  let hideTimeout = null;
  let showTimeout = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function buildCard(author) {
    card.innerHTML = "";
    card.href = "/profil/" + encodeURIComponent(author.slug);
    const header = el("div", "author-card-header");
    if (author.profile_image) {
      const img = el("img", "author-card-avatar");
      img.src = author.profile_image;
      img.alt = author.display_name;
      header.appendChild(img);
    } else {
      const fallback = el("span", "author-card-avatar author-card-avatar-fallback", author.display_name.charAt(0));
      header.appendChild(fallback);
    }
    const nameWrap = el("div", "author-card-namewrap");
    nameWrap.appendChild(el("div", "author-card-name", author.display_name));
    if (author.editorial_role) {
      nameWrap.appendChild(el("div", "author-card-role" + (author.is_opinion_columnist ? " is-columnist" : ""), author.editorial_role));
    }
    header.appendChild(nameWrap);
    card.appendChild(header);

    if (author.short_bio) {
      card.appendChild(el("p", "author-card-bio", author.short_bio));
    }

    if (author.twitter_handle || author.minecraft_username) {
      const meta = el("div", "author-card-meta");
      if (author.twitter_handle) {
        const tw = el("span", "author-card-meta-item");
        tw.appendChild(el("span", "author-card-meta-label", "X:"));
        tw.appendChild(document.createTextNode(" @" + author.twitter_handle));
        meta.appendChild(tw);
      }
      if (author.minecraft_username) {
        const mc = el("span", "author-card-meta-item");
        mc.appendChild(el("span", "author-card-meta-label", "Minecraft:"));
        mc.appendChild(document.createTextNode(" " + author.minecraft_username));
        meta.appendChild(mc);
      }
      card.appendChild(meta);
    }

    card.appendChild(el("span", "author-card-cta", "Profili görüntüle →"));
  }

  function position(anchor) {
    const margin = 10;
    const rect = anchor.getBoundingClientRect();
    card.style.top = "-9999px";
    card.style.left = "-9999px";
    card.hidden = false;
    const cardRect = card.getBoundingClientRect();

    let top = rect.bottom + margin;
    if (top + cardRect.height > window.innerHeight - margin) {
      top = rect.top - cardRect.height - margin;
      if (top < margin) top = margin;
    }
    let left = rect.left;
    if (left + cardRect.width > window.innerWidth - margin) {
      left = rect.right - cardRect.width;
    }
    if (left < margin) left = margin;

    card.style.top = top + "px";
    card.style.left = left + "px";
  }

  function show(link) {
    const id = link.dataset.authorId;
    const author = authors[id];
    if (!author) return;
    clearTimeout(hideTimeout);
    activeLink = link;
    buildCard(author);
    position(link);
    card.classList.add("visible");
  }

  function scheduleShow(link) {
    clearTimeout(showTimeout);
    showTimeout = setTimeout(() => show(link), 180);
  }

  function scheduleHide() {
    clearTimeout(showTimeout);
    hideTimeout = setTimeout(hide, 150);
  }

  function hide() {
    card.classList.remove("visible");
    card.hidden = true;
    activeLink = null;
  }

  document.addEventListener("mouseover", (e) => {
    const link = e.target.closest(".author-link");
    if (link && link.dataset.authorId) scheduleShow(link);
  });
  document.addEventListener("mouseout", (e) => {
    const link = e.target.closest(".author-link");
    if (link) scheduleHide();
  });
  document.addEventListener("focusin", (e) => {
    const link = e.target.closest(".author-link");
    if (link && link.dataset.authorId) show(link);
  });
  document.addEventListener("focusout", (e) => {
    const link = e.target.closest(".author-link");
    if (link) scheduleHide();
  });
  card.addEventListener("mouseover", () => clearTimeout(hideTimeout));
  card.addEventListener("mouseout", scheduleHide);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !card.hidden) {
      hide();
      if (activeLink) activeLink.blur();
    }
  });
  document.addEventListener("scroll", () => { if (!card.hidden && activeLink) position(activeLink); }, true);
})();
