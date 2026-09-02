"use client";

import { useEffect, useSyncExternalStore, type RefObject } from "react";

/**
 * Escape closes the thing on top.
 *
 * Only the evidence drawer listened for Escape, so most overlays could be dismissed with the mouse
 * and none with the keyboard. That is a real problem on the two screens where it matters most: the
 * insurance packet and the Parallel console are both full-screen overlays a reader opens to
 * *inspect* something, and the reflex when you are done reading is Escape.
 *
 * (The original note here claimed every modal also closed on a backdrop click. Two did not — the two
 * in the top bar, which are the first ones a visitor opens. Both now do.)
 *
 * The listener is bound only while the overlay is open, so stacked overlays do not fight: the outer
 * one is unmounted or closed by the time the inner one is gone, and while both are open the inner
 * one's handler runs last and its `stopPropagation`-free close is the one the user meant.
 */
export function useDismissOnEscape(open: boolean, onClose: () => void): void {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
}

/**
 * Keep the keyboard inside an open overlay, and give focus back when it closes.
 *
 * Without this, an overlay is a visual modal and nothing more: Tab walks straight out of it into the
 * page behind — for the non-portalled ones, forward through the entire document — and closing leaves
 * focus on `document.body`, so the next Tab restarts at the top of the page rather than returning to
 * the control that opened it. A keyboard user has to re-traverse the whole page after every glance
 * at a dossier.
 *
 * Deliberately small: it moves focus in on open, cycles Tab within the container, and restores the
 * previously focused element on close. It does not mark the background `inert` — that would need the
 * overlay to own the whole page, and two of these render inline inside the page tree.
 */
export function useFocusTrap(open: boolean, ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    if (!open) return;
    const container = ref.current;
    const previously = document.activeElement as HTMLElement | null;

    const focusable = () =>
      Array.from(
        container?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter((el) => el.offsetParent !== null || el === document.activeElement);

    // Focus the container itself rather than its first control: landing on a "Close" button reads as
    // "you are about to close this", which is not what opening something means.
    container?.focus?.();

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === container)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      // Only take focus back if it is still somewhere inside the overlay we are closing.
      if (previously && (!document.activeElement || container?.contains(document.activeElement) || document.activeElement === document.body)) {
        previously.focus?.();
      }
    };
  }, [open, ref]);
}

/**
 * True once the component has mounted in a browser, false during server rendering.
 *
 * The reason this exists rather than the usual `useState(false)` + `useEffect(() => setMounted(true))`
 * is that the usual version sets state synchronously inside an effect, which React 19 flags as a
 * cascading render — it was two of the two lint errors in this app. `useSyncExternalStore` expresses
 * the same idea without the extra render: there is nothing to subscribe to, the client snapshot is
 * `true` and the server snapshot is `false`, so the first client render already has the answer.
 *
 * Used to gate `createPortal`, which needs a DOM that does not exist during SSR.
 */
export function useMounted(): boolean {
  return useSyncExternalStore(subscribeToNothing, () => true, () => false);
}

/** No external store to watch — the value is constant per environment. */
const subscribeToNothing = () => () => {};
