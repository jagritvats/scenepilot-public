"use client";

import { useEffect, useMemo, useRef, useState } from "react";

/** How long to keep looking for a section that has not rendered yet. */
const FIND_MS = 4000;
/** How long to hold the page on that section while panels above it are still arriving. */
const HOLD_MS = 2500;
/**
 * How long the target has to sit still under a correction before the page counts as settled.
 * Wall clock, not a frame count: a frame count is a different amount of time on a 60 Hz laptop and a
 * 240 Hz monitor, and the number has to outlast the slowest of the eight mount-time fetches that
 * render above the anchor on a shoot day. The old five-frame streak let go ~80 ms in, which is before
 * any of them land.
 */
const STILL_MS = 1200;
/**
 * How often to notice a fragment the browser never announced. See `watchHash` — nothing in the
 * platform reports a pushState, so the only way to hear one is to look.
 */
const HASH_POLL_MS = 120;

/** Keys that mean "I want to move the page", and are therefore an instruction to stop correcting it. */
const SCROLL_KEYS = new Set([
  "ArrowUp",
  "ArrowDown",
  "ArrowLeft",
  "ArrowRight",
  "PageUp",
  "PageDown",
  "Home",
  "End",
  " ",
  "Spacebar",
]);

/** Typing in a field is not scrolling, whatever the key was. */
function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!target.closest("[contenteditable]");
}

/**
 * Call `onChange` whenever the URL fragment changes, including the changes nothing announces.
 *
 * `hashchange` is not enough on its own. Next intercepts clicks on a local `<Link>` and navigates by
 * calling `history.pushState` (app-router.js patches pushState so `usePathname` keeps up, and
 * restores it on unmount — which is why patching it here would either be clobbered or leak). The
 * platform fires no event for pushState, and `grep hashchange node_modules/next/dist/client` returns
 * nothing, so a tour step pointing at `#recovery` clicked while already on that day used to change
 * the URL and nothing else. `popstate` covers back/forward only. So the fragment is also polled: two
 * string comparisons every eighth of a second, which is the price of hearing a same-document
 * navigation at all.
 *
 * App Router's own hooks are no help — `usePathname` and `useSearchParams` do not carry the fragment.
 */
export function watchHash(onChange: () => void): () => void {
  let seen = window.location.hash;
  const check = () => {
    if (window.location.hash === seen) return;
    seen = window.location.hash;
    onChange();
  };
  window.addEventListener("hashchange", check);
  window.addEventListener("popstate", check);
  const poll = window.setInterval(check, HASH_POLL_MS);
  return () => {
    window.removeEventListener("hashchange", check);
    window.removeEventListener("popstate", check);
    window.clearInterval(poll);
  };
}

export interface HashScrollState {
  /**
   * The section the URL asked for, when it did not exist and a fallback was scrolled to instead —
   * so a page can say "that part is not here yet, here is where it starts". Null when the requested
   * section was found, and null before anything has been resolved.
   */
  fellBackFrom: string | null;
}

/**
 * Scroll to the `#section` in the URL, and stay there while the page finishes loading.
 *
 * A browser does this for free on a static document and does nothing useful here: every deep-linked
 * section on a shoot day lives behind an await. The day, its run and the recovery options in it all
 * arrive after hydration, so at the moment the browser goes looking for `#stripboard` the page is
 * still a loading shimmer and there is nothing to scroll to. The link navigates, the reader lands at
 * the top of a page that looks identical to the one the last card sent them to, and the deep link
 * reads as broken rather than as slow.
 *
 * Waiting for the element is only half of it. Ephemeris, labour packs, weather and the ripple panel
 * all resolve separately and expand the page *above* the target, so a single scroll is correct for
 * about one frame. So the position is re-asserted every frame until it stops moving — or until the
 * reader scrolls, which ends it immediately: nothing here is worth fighting someone's own wheel.
 *
 * `fallbacks` maps a section that only exists in some states onto one that always does. `#recovery`
 * is real only once a disruption has been reported; sending that link to the disruption picker — the
 * place the reader has to start anyway — is a better answer than not moving at all. When that
 * happens the requested id comes back as `fellBackFrom`, and the URL is rewritten to the section
 * actually landed on, so a reload or a shared link does not repeat the claim.
 *
 * The returned object is additive: callers that only want the scrolling can ignore it.
 */
export function useHashScroll(ready: boolean, fallbacks: Record<string, string> = {}): HashScrollState {
  // Serialised so a fresh object literal at the call site does not restart the search every render.
  const fallbackKey = JSON.stringify(fallbacks);
  const [fellBackFrom, setFellBackFrom] = useState<string | null>(null);
  // Read inside the effect only, to keep the effect off the state it sets.
  const reported = useRef<string | null>(null);
  // The fragment this hook wrote itself, so neither the watcher below nor a second run of this effect
  // reads our own correction as a new request and clears the very hint it just produced. A ref rather
  // than an effect-local, because StrictMode runs the effect twice and the second run would otherwise
  // resolve the rewritten `#disruption` as a section that was asked for and found.
  const selfWritten = useRef("");

  useEffect(() => {
    if (!ready) return;
    const map: Record<string, string> = JSON.parse(fallbackKey);
    let frame = 0;

    const announce = (from: string | null) => {
      if (reported.current === from) return;
      reported.current = from;
      setFellBackFrom(from);
    };

    /** The element the URL is asking for, plus which id actually answered. */
    const resolve = () => {
      const id = decodeURIComponent(window.location.hash.slice(1));
      if (!id) return null;
      const direct = document.getElementById(id);
      if (direct) return { el: direct, landedOn: id, fellBackFrom: null as string | null };
      const alt = map[id];
      const el = alt ? document.getElementById(alt) : null;
      return el ? { el, landedOn: alt, fellBackFrom: id } : null;
    };

    // `history.replaceState`, never `location.hash = ...`: assigning to the hash pushes a history
    // entry the reader never asked for and fires hashchange, which re-enters start() on the section
    // we just corrected to. Passing the existing history state keeps Next's own internals on the
    // entry — its patched replaceState copies them anyway, but only the pathname and query it tracks
    // are affected, and a fragment rewrite has no business dispatching a router restore.
    const rewrite = (landedOn: string) => {
      const { pathname, search } = window.location;
      window.history.replaceState(window.history.state, "", `${pathname}${search}#${landedOn}`);
      selfWritten.current = window.location.hash;
    };

    const start = () => {
      if (!window.location.hash) return;
      // Anything other than our own correction is a fresh request, and gets to say so again.
      if (window.location.hash !== selfWritten.current) selfWritten.current = "";
      cancelAnimationFrame(frame);
      const giveUp = Date.now() + FIND_MS;
      let holdUntil = 0;
      let stillSince = 0;
      let landed = false;

      const hunt = () => {
        const hit = resolve();
        if (!hit) {
          if (Date.now() < giveUp) frame = requestAnimationFrame(hunt);
          return;
        }
        if (!landed) {
          landed = true;
          // Skipped when this hunt is only re-asserting a fragment we wrote ourselves: the section
          // now in the URL was found, but it is not the one the reader asked for, and saying so
          // would retract a hint that is still true.
          if (!selfWritten.current) {
            announce(hit.fellBackFrom);
            if (hit.fellBackFrom) rewrite(hit.landedOn);
          }
        }
        // Measured before the correction, not after. Reading the rect after scrollIntoView only ever
        // reports the offset the correction itself just imposed — the scroll-mt-20 on the section, 80
        // on every frame by construction, since nothing in this app sets scroll-behavior: smooth and
        // scrollIntoView is therefore synchronous. That agreement is what used to declare the page
        // settled ~80 ms in, before a single one of the fetches rendering above the anchor had landed.
        const before = Math.round(hit.el.getBoundingClientRect().top);
        // Instant, and repeated — the same thing a plain anchor does, held against the layout
        // shifting underneath it. `block: "start"` honours each section's `scroll-mt`, which is what
        // keeps the target clear of the sticky top bar.
        hit.el.scrollIntoView({ block: "start" });
        const after = Math.round(hit.el.getBoundingClientRect().top);
        const now = Date.now();
        if (!holdUntil) holdUntil = now + HOLD_MS;
        // Settled is measured on the target's own position, not on `window.scrollY`: `overflow-x:
        // hidden` on the body makes the body a scroll container too, so which element actually moved
        // is not something worth asserting from here.
        if (before !== after) stillSince = 0;
        else if (!stillSince) stillSince = now;
        const settled = stillSince > 0 && now - stillSince >= STILL_MS;
        if (!settled && now < holdUntil) frame = requestAnimationFrame(hunt);
      };

      hunt();
    };

    const stop = () => cancelAnimationFrame(frame);
    // Filtered, because the previous version stopped on any key at all: Tab out of the page, Escape a
    // modal or hit Ctrl+K for the palette within the first two seconds and the deep link quietly gave
    // up, with nothing to re-arm it. A keystroke only counts as an instruction to stop when it is one
    // that scrolls, and not when it is being typed into something.
    const onKeyDown = (e: KeyboardEvent) => {
      if (!SCROLL_KEYS.has(e.key)) return;
      if (isEditable(e.target)) return;
      stop();
    };

    start();
    // Panels below appear and disappear as the rescue runs, so a hash clicked later in the session
    // gets the same treatment as one arrived with — including a tour step clicked from the page it
    // points at, which Next navigates with pushState and no event of any kind.
    const unwatch = watchHash(() => {
      if (window.location.hash === selfWritten.current) return;
      start();
    });
    window.addEventListener("wheel", stop, { passive: true });
    window.addEventListener("touchstart", stop, { passive: true });
    // Named, and removed by the same reference: an inline arrow here would leak the listener.
    window.addEventListener("keydown", onKeyDown);
    return () => {
      cancelAnimationFrame(frame);
      unwatch();
      window.removeEventListener("wheel", stop);
      window.removeEventListener("touchstart", stop);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [ready, fallbackKey]);

  return useMemo(() => ({ fellBackFrom }), [fellBackFrom]);
}
