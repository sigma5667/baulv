import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { SupportChat } from "../components/SupportChat";
import { joinWaitlist } from "../api/waitlist";
import { BEISPIEL, type BeispielRaum } from "../landing/beispiel";
// scroll-craft-Engine (unverändert übernommen). Das IIFE definiert nur
// window.ScrollCraft; gemountet wird erst in der LandingPage.
import "../landing/scrollcraft.js";
// Beide Stylesheets werden NICHT global geladen, sondern nur während die
// Landing gerendert ist als <style> eingehängt (siehe useLandingStyles).
import engineCss from "../landing/scrollcraft.css?inline";
import pageCss from "../landing/landing.css?inline";

/*
 * Öffentliche Startseite "/" (scroll-craft, Grammatik "Split stage").
 *
 * Alle Bilder sind echte, anonymisierte Screens der BauLV-App
 * (public/landing/), die Beispielwerte stammen aus einem anonymisierten
 * Projekt (src/landing/beispiel.ts). Keine Fremd-Requests: Schrift
 * IBM Plex Sans über @fontsource, alle Assets vom eigenen Origin.
 *
 * Sichtbarkeit unverändert: App.tsx rendert diese Seite nur mit
 * VITE_PUBLIC_LANDING=true ohne Beta-Gate, sonst hinter <PublicWithGate>.
 *
 * Seitenwechsel von hier weg passieren über native <a href>-Links
 * (Vollladen). Die Engine hat kein destroy(); so läuft sie nie im
 * Hintergrund einer anderen Route weiter.
 */

declare global {
  interface Window {
    ScrollCraft?: { mount: (root: Element, opts?: unknown) => unknown };
  }
}

const fmt = (n: number, d = 2) =>
  n.toFixed(d).replace(".", ",").replace(/\B(?=(\d{3})+(?!\d))/g, ".");

function useLandingStyles() {
  useEffect(() => {
    const el = document.createElement("style");
    el.setAttribute("data-landing", "");
    el.textContent = engineCss + "\n" + pageCss;
    document.head.appendChild(el);
    return () => {
      el.remove();
      document.documentElement.classList.remove("sc-ready");
      document.documentElement.style.removeProperty("--sc-canvas");
    };
  }, []);
}

/* ---------------------------------------------------------------------- */
/* Prüfen: der Besucher wählt einen echten Raum, rechts steht sein Nachweis */
/* ---------------------------------------------------------------------- */

const KIND: Record<string, string> = { fenster: "Fenster", tuer: "Tür", "tür": "Tür" };

function DedLine({ d }: { d: { o: string; a: number } }) {
  const m = /^(\w+)\s+([\d.]+)×([\d.]+)\s+\((\d+)×\)/.exec(d.o) ?? [];
  const label = `${KIND[m[1] as string] ?? m[1] ?? "Öffnung"} ${fmt(+m[2])} × ${fmt(+m[3])}${
    +m[4] > 1 ? ` (${m[4]}×)` : ""
  }`;
  // Bewusst ohne Urteil "abgezogen/übermessen": die Abzugsregel
  // (2,5 vs. 5,0 m²) ist fachlich noch offen. Gezeigt wird nur, was
  // erfasst wurde.
  return (
    <div className="proof__line ded">
      <span>
        {label} <span className="why">erfasst</span>
      </span>
      <span>{fmt(d.a, 3)} m²</span>
    </div>
  );
}

function ProofPanel({ r }: { r: BeispielRaum }) {
  const expr = (e: string) => e.split("×").map((x) => fmt(+x, 3)).join(" × ");
  const w = r.w!;
  return (
    <div className="panel" aria-live="polite">
      <p className="label">{r.u}</p>
      <h3>{r.n}</h3>
      <div className="proof__pos">
        <b>01.01</b>
        <span>Wandbeschichtung</span>
      </div>
      <div className="proof__line">
        <span>{w.f}</span>
        <span>
          {expr(w.e)} = <b>{fmt(w.raw, 3)} m²</b>
        </span>
      </div>
      {w.d.length ? (
        w.d.map((d, i) => <DedLine key={i} d={d} />)
      ) : (
        <div className="proof__line ded">
          <span className="why">Keine Öffnungen erfasst</span>
          <span />
        </div>
      )}
      <div className="net">
        <span>Rohmaß Wand</span>
        <span className="num">{fmt(w.raw, 3)} m²</span>
      </div>
      {r.c && (
        <>
          <div className="proof__pos" style={{ marginTop: 8 }}>
            <b>02.01</b>
            <span>Deckenbeschichtung</span>
          </div>
          <div className="proof__line">
            <span>{r.c.f}</span>
            <span className="num">
              <b>{fmt(r.c.net, 3)} m²</b>
            </span>
          </div>
        </>
      )}
    </div>
  );
}

function RoomPicker() {
  const rooms = BEISPIEL.rooms.filter((r) => r.w);
  const start = Math.max(0, rooms.findIndex((r) => r.n === "Wohnen" && r.p === 23));
  const [sel, setSel] = useState(start);
  const units = [...new Set(rooms.map((r) => r.u))];
  return (
    <>
      <div className="check__plan">
        <div className="sc-stack" data-sc-in data-sc-stagger="60">
          <h2 className="h-act" id="pruefen-h">
            Jeder Raum, eine Zeile.
          </h2>
          <p className="small">
            Wählen Sie einen Raum aus dem Beispielprojekt. Rechts steht sein Rechenweg, so wie
            BauLV ihn ablegt.
          </p>
        </div>
        <div className="rooms">
          {units.map((u) => (
            <fieldset key={u}>
              <legend className="label">{u}</legend>
              <div className="grid">
                {rooms.map((r, i) =>
                  r.u !== u ? null : (
                    <button
                      key={i}
                      type="button"
                      className="room"
                      aria-pressed={i === sel}
                      onClick={() => setSel(i)}
                    >
                      <b>{r.n}</b>
                      <span>
                        {fmt(r.a)} m² · Umfang {fmt(r.p)} m
                      </span>
                    </button>
                  ),
                )}
              </div>
            </fieldset>
          ))}
        </div>
      </div>
      <div className="check__lv">
        <div className="sticky">
          <p className="label">Berechnungsnachweis</p>
          <ProofPanel r={rooms[sel]} />
          <p className="small note">
            Beispielprojekt aus der BauLV-App: echter Grundriss, echte Werte, Namen
            anonymisiert. Werte aus der KI-Plananalyse prüfen Sie vor der Verwendung im LV.
          </p>
        </div>
      </div>
    </>
  );
}

/* ---------------------------------------------------------------------- */
/* Seiten-Choreografie: Fuge, Hero-Tiefe, Übergabe der Maße (Peak)          */
/* ---------------------------------------------------------------------- */

function useSplitStage(rootRef: React.RefObject<HTMLDivElement | null>) {
  useEffect(() => {
    const host = rootRef.current;
    if (!host) return;
    const root = document.documentElement;
    const phone = matchMedia("(max-width: 860px)");
    const still = matchMedia("(prefers-reduced-motion: reduce)");
    const fine = matchMedia("(hover: hover) and (pointer: fine)");
    const fuge = host.querySelector<HTMLElement>(".fuge")!;
    const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));
    const ease = (t: number) => t * t * (3 - 2 * t);

    // Engine einmal pro DOM mounten (StrictMode führt Effekte doppelt aus).
    const h = host as HTMLDivElement & { __sc?: boolean };
    if (!h.__sc && window.ScrollCraft) {
      window.ScrollCraft.mount(host);
      h.__sc = true;
    }

    type Act = { el: HTMLElement; pinned: boolean; kf: number[][]; top: number; h: number };
    const acts: Act[] = [...host.querySelectorAll<HTMLElement>("[data-split]")].map((el) => ({
      el,
      pinned: el.dataset.scAct !== "flow",
      kf: (el.dataset.split ?? "0:50").split(",").map((s) => s.split(":").map(Number)),
      top: 0,
      h: 0,
    }));
    let pts: number[][] = [];
    let vw = 0;
    let vh = 0;

    // ---- the peak: chips leave the plan row and land in the proof ----
    const peak = acts.find((a) => a.el.id === "uebergabe")!;
    const stage = peak.el.querySelector<HTMLElement>("[data-sc-stage]")!;
    const fly = peak.el.querySelector<HTMLElement>(".flyers")!;
    const net = host.querySelector<HTMLElement>("#r-net")!;
    const rowmark = peak.el.querySelector<HTMLElement>(".rowmark")!;
    type Leg = {
      s: HTMLElement; d: HTMLElement; f: HTMLElement; res: HTMLElement[];
      a: number; b: number; x0: number; y0: number; x1: number; y1: number;
    };
    const legs: Leg[] = (
      [
        ["c-umfang", "s-umfang", 0.12, 0.3, []],
        ["c-hoehe", "s-hoehe", 0.22, 0.4, ["r-raw"]],
        ["c-fenster", "s-fenster", 0.46, 0.62, ["r-fenster", "r-fenster-why"]],
        ["c-tuer", "s-tuer", 0.54, 0.7, ["r-tuer", "r-tuer-why"]],
      ] as [string, string, number, number, string[]][]
    ).map(([src, dst, a, b, after]) => {
      const s = host.querySelector<HTMLElement>("#" + src)!;
      const f = s.cloneNode(true) as HTMLElement;
      f.removeAttribute("id");
      f.removeAttribute("style");
      f.className = "chip flyer";
      fly.appendChild(f);
      return {
        s, f, a, b, d: host.querySelector<HTMLElement>("#" + dst)!,
        res: after.map((id) => host.querySelector<HTMLElement>("#" + id)!),
        x0: 0, y0: 0, x1: 0, y1: 0,
      };
    });

    function measurePeak() {
      const sr = stage.getBoundingClientRect();
      for (const L of legs) {
        const s = L.s.getBoundingClientRect();
        const d = L.d.getBoundingClientRect();
        const f = L.f.getBoundingClientRect();
        L.x0 = s.left - sr.left;
        L.y0 = s.top - sr.top;
        L.x1 = d.left - sr.left + Math.max(0, d.width - f.width);
        L.y1 = d.top - sr.top + (d.height - f.height) / 2;
      }
    }
    function measure() {
      vw = root.clientWidth;
      vh = innerHeight;
      pts = [];
      for (const a of acts) {
        a.top = a.el.getBoundingClientRect().top + scrollY;
        a.h = a.el.offsetHeight;
        const travel = a.pinned ? Math.max(a.h - vh, 1) : a.h;
        for (const [t, v] of a.kf) {
          const y = a.pinned ? a.top + t * travel : a.top - vh * 0.4 + t * travel;
          pts.push([y, phone.matches && v !== 0 ? 50 : v]);
        }
        if (!a.pinned) pts.push([a.top + a.h - vh * 0.6, a.kf[a.kf.length - 1][1]]);
      }
      pts.sort((x, y) => x[0] - y[0]);
      measurePeak();
      last = -1;
      lastP = -1;
    }
    function splitAt(y: number) {
      if (!pts.length) return 50;
      if (y <= pts[0][0]) return pts[0][1];
      for (let i = 1; i < pts.length; i++) {
        if (y <= pts[i][0]) {
          const [y0, v0] = pts[i - 1];
          const [y1, v1] = pts[i];
          if (still.matches) return v1;
          return v0 + (v1 - v0) * ease(clamp((y - y0) / Math.max(y1 - y0, 1), 0, 1));
        }
      }
      return pts[pts.length - 1][1];
    }
    const progress = (a: Act) => clamp((scrollY - a.top) / Math.max(a.h - vh, 1), 0, 1);

    function drawPeak(p: number) {
      let sig = "";
      rowmark.style.setProperty("--rm", clamp(p / 0.08, 0, 1).toFixed(2));
      for (const L of legs) {
        const t = clamp((p - L.a) / (L.b - L.a), 0, 1);
        const e = ease(t);
        const x = L.x0 + (L.x1 - L.x0) * e;
        const y = L.y0 + (L.y1 - L.y0) * e - Math.sin(Math.PI * e) * (phone.matches ? 24 : 70);
        L.f.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
        L.f.style.opacity = t > 0 && t < 1 ? "1" : "0";
        L.s.style.opacity = t > 0 ? "0.45" : "1";
        L.d.style.setProperty("--o", clamp((t - 0.85) / 0.15, 0, 1).toFixed(2));
        const out = clamp((p - L.b) / 0.05, 0, 1);
        for (const r of L.res) r.style.setProperty("--o", out.toFixed(2));
        sig += Math.round(e * 20) + "." + Math.round(out * 4) + ";";
      }
      const n = clamp((p - 0.76) / 0.06, 0, 1);
      net.style.setProperty("--o", n.toFixed(2));
      stage.setAttribute("data-sc-verify-state", sig + Math.round(n * 4));
    }

    // ---- hero depth ----
    const hero = acts[0];
    const planes = [...hero.el.querySelectorAll<HTMLElement>(".plane")];
    let mx = 0, my = 0, tx = 0, ty = 0;
    const onPointer = (e: PointerEvent) => {
      if (!fine.matches || still.matches) return;
      tx = e.clientX / innerWidth - 0.5;
      ty = e.clientY / innerHeight - 0.5;
    };

    let last = -1;
    let lastP = -1;
    let raf = 0;
    const check = acts.find((a) => a.el.id === "pruefen")!;
    function frame() {
      mx += (tx - mx) * 0.08;
      my += (ty - my) * 0.08;
      const y = scrollY;
      if (y !== last || Math.abs(tx - mx) > 0.001 || Math.abs(ty - my) > 0.001) {
        last = y;
        const s = splitAt(y);
        const horizontal = !phone.matches;
        const len = horizontal ? vw : vh;
        const px = (s / 100) * len;
        root.style.setProperty(horizontal ? "--sx" : "--sy", px.toFixed(1) + "px");
        root.style.setProperty(horizontal ? "--sy" : "--sx", horizontal ? "50svh" : "50vw");
        root.style.setProperty("--shift", ((px - len / 2) / 2).toFixed(1) + "px");
        const doc = Math.max(document.documentElement.scrollHeight - vh, 1);
        const along = horizontal ? 70 + (y / doc) * (vh - 110) : 16 + (y / doc) * (vw * 0.62);
        root.style.setProperty(horizontal ? "--my" : "--mx", along.toFixed(1) + "px");
        // Fuge nur über der Split-Bühne zeigen, nicht über Formular und Footer.
        const end = acts[acts.length - 1];
        const past = y > end.top + end.h - vh * 1.02;
        const inCheckPhone = phone.matches && y > check.top - vh * 0.5 && y < check.top + check.h - vh * 0.5;
        fuge.dataset.hidden = String(past || inCheckPhone);
        root.style.setProperty("--closeX", horizontal ? (px / 2).toFixed(1) + "px" : "0px");
        const hp = progress(hero);
        if (!still.matches)
          for (const el of planes) {
            const d = +(el.dataset.depth ?? 1);
            el.style.transform = `translate(${(mx * 14 * d).toFixed(1)}px, ${(-hp * 120 * d + my * 10 * d).toFixed(1)}px)`;
          }
        const pp = still.matches ? 1 : progress(peak);
        if (pp !== lastP) {
          lastP = pp;
          drawPeak(pp);
        }
      }
      raf = requestAnimationFrame(frame);
    }

    measure();
    addEventListener("pointermove", onPointer, { passive: true });
    addEventListener("resize", measure);
    addEventListener("load", measure);
    phone.addEventListener("change", measure);
    document.fonts?.ready.then(measure);
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      removeEventListener("pointermove", onPointer);
      removeEventListener("resize", measure);
      removeEventListener("load", measure);
      phone.removeEventListener("change", measure);
      for (const L of legs) L.f.remove();
      for (const k of ["--sx", "--sy", "--shift", "--my", "--mx", "--closeX"]) root.style.removeProperty(k);
    };
  }, [rootRef]);
}

/* ---------------------------------------------------------------------- */
/* Warteliste (v25) — unverändert übernommen                              */
/* ---------------------------------------------------------------------- */


/**
 * Wortlaut der Einwilligungs-Checkbox — MUSS zeichengleich mit
 * ``WAITLIST_CONSENT_TEXT`` in ``backend/app/services/waitlist.py``
 * bleiben (dort als Version gepinnt; jede Anmeldung referenziert
 * die Version als Art.-7-Nachweis). Beim Ändern beide Stellen
 * gemeinsam bumpen.
 */
const WAITLIST_CONSENT_TEXT =
  "Ich möchte per E-Mail über den Entwicklungsstand und den Start " +
  "von BauLV informiert werden. Die Einwilligung kann ich jederzeit " +
  "über den Abmelde-Link widerrufen.";

/** ``?ref=``-Wert auf das Backend-Whitelist-Alphabet vorfiltern. */
const SOURCE_RE = /^[a-z0-9_-]{1,64}$/;

const INPUT_CLASSES =
  "w-full border border-slate-300 bg-white px-3 py-2.5 text-sm " +
  "focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary";

/**
 * Warteliste-Formular (v25): E-Mail + Firma Pflicht, Name optional,
 * Einwilligung als eigene, NICHT vorangehakte Checkbox. Nach dem
 * Absenden ersetzt der "Prüfen Sie Ihr Postfach"-Zustand das
 * Formular — bewusst KEINE Aussage darüber, ob die Adresse schon
 * gelistet war (das Backend antwortet in jedem Zweig identisch).
 */
function WaitlistForm() {
  const [searchParams] = useSearchParams();
  // Kampagnen-Herkunft aus ?ref= — einmal pro Mount gelesen.
  const source = useMemo(() => {
    const raw = (searchParams.get("ref") ?? "").trim().toLowerCase();
    return SOURCE_RE.test(raw) ? raw : null;
  }, [searchParams]);

  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [name, setName] = useState("");
  const [consent, setConsent] = useState(false);
  const [loading, setLoading] = useState(false);
  const [doneMessage, setDoneMessage] = useState("");
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!consent) {
      setError(
        "Bitte bestätigen Sie die Einwilligung, per E-Mail informiert zu werden.",
      );
      return;
    }
    setLoading(true);
    try {
      const message = await joinWaitlist({
        email: email.trim(),
        company_name: company.trim(),
        name: name.trim() || null,
        consent,
        source,
      });
      setDoneMessage(message);
    } catch (err: any) {
      if (err?.response?.status === 429) {
        setError(
          "Zu viele Versuche. Bitte versuchen Sie es in einigen Minuten erneut.",
        );
      } else {
        setError(
          typeof err?.response?.data?.detail === "string"
            ? err.response.data.detail
            : "Das hat leider nicht geklappt. Bitte versuchen Sie es später erneut.",
        );
      }
    } finally {
      setLoading(false);
    }
  };

  if (doneMessage) {
    return (
      <div className="border border-slate-200 bg-white p-6 sm:p-8">
        <p className="font-plexmono text-[11px] uppercase tracking-wider text-primary">
          Prüfen Sie Ihr Postfach
        </p>
        <p className="mt-3 text-slate-700">{doneMessage}</p>
        <p className="mt-4 text-sm leading-relaxed text-slate-500">
          Der Bestätigungslink ist 7 Tage gültig. Ohne Bestätigung
          erhalten Sie keine weiteren E-Mails, und Ihr Eintrag wird
          automatisch gelöscht.
        </p>
      </div>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="border border-slate-200 bg-white p-6 sm:p-8"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label htmlFor="wl-email" className="mb-1 block text-sm font-medium">
            E-Mail-Adresse
          </label>
          <input
            id="wl-email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={INPUT_CLASSES}
            placeholder="name@firma.at"
          />
        </div>
        <div>
          <label
            htmlFor="wl-company"
            className="mb-1 block text-sm font-medium"
          >
            Firma
          </label>
          <input
            id="wl-company"
            type="text"
            required
            minLength={2}
            maxLength={200}
            autoComplete="organization"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            className={INPUT_CLASSES}
            placeholder="Muster Bau GmbH"
          />
        </div>
        <div>
          <label htmlFor="wl-name" className="mb-1 block text-sm font-medium">
            Name{" "}
            <span className="font-normal text-slate-400">(optional)</span>
          </label>
          <input
            id="wl-name"
            type="text"
            maxLength={200}
            autoComplete="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className={INPUT_CLASSES}
          />
        </div>
      </div>

      {/* Einwilligung — eigene Box, nie vorangehakt. Wortlaut ist
          der gepinnte Consent-Text (siehe Konstante oben). */}
      <label className="mt-4 flex cursor-pointer items-start gap-3 border border-slate-300 p-4">
        <input
          type="checkbox"
          required
          checked={consent}
          onChange={(e) => setConsent(e.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 accent-primary"
        />
        <span className="text-sm leading-relaxed text-slate-700">
          {WAITLIST_CONSENT_TEXT}{" "}
          {/* Natives <a>: die Landing verlässt man per Seitenwechsel,
              damit die Scroll-Engine nicht im Hintergrund weiterläuft. */}
          <a
            href="/datenschutz"
            className="text-primary hover:underline"
            onClick={(e) => e.stopPropagation()}
          >
            Hinweise zum Datenschutz
          </a>
        </span>
      </label>

      {error && (
        <p className="mt-4 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={loading}
        className="mt-5 inline-flex w-full items-center justify-center gap-2 bg-primary px-6 py-3 text-base font-medium text-primary-foreground hover:bg-blue-700 disabled:opacity-50 sm:w-auto"
      >
        {loading ? "Wird eingetragen..." : "Auf die Warteliste"}
        <ArrowRight className="h-5 w-5" />
      </button>
      <p className="mt-3 text-xs leading-relaxed text-slate-500">
        Unverbindlich — es entsteht kein Vertrag, es fallen keine
        Kosten an. Abmeldung jederzeit über den Link in jeder E-Mail.
      </p>
    </form>
  );
}

/* ---------------------------------------------------------------------- */
/* Seite                                                                  */
/* ---------------------------------------------------------------------- */

const CTA = "Kostenlos testen";

export function LandingPage() {
  const rootRef = useRef<HTMLDivElement>(null);
  useLandingStyles();
  useSplitStage(rootRef);

  return (
    <div className="sc-landing" ref={rootRef}>
      <span data-sc-progress />

      <div className="fuge" aria-hidden="true">
        <div className="fuge__ticks" />
        <div className="fuge__line" />
        <div className="fuge__head">
          <span>Plan</span>
          <span>LV</span>
        </div>
        <div className="fuge__mark" />
      </div>

      <main>
        {/* 1 · Gegenüberstellung */}
        <section className="hero" data-sc-act="pin" data-sc-span="1.6" data-split="0:50">
          <h1 className="sr-only-l">Malerflächen aus Ihrem Plan – Raum für Raum nachrechenbar.</h1>
          <div data-sc-stage>
            <div className="side side--plan">
              <div className="half">
                <div className="pad">
                  <figure className="sheet sheet--far plane" data-depth="0.35">
                    <img src="/landing/plananalyse.webp" width={1800} height={1319} alt="BauLV Plananalyse: hochgeladener Grundriss und Liste der erkannten Räume" />
                  </figure>
                  <figure className="sheet sheet--near plane" data-depth="1">
                    <img src="/landing/raeume.webp" width={1800} height={1268} alt="Tabelle der aus dem Plan erkannten Räume mit Fläche, Umfang und Raumhöhe" />
                  </figure>
                  <a className="brand" href="/">
                    <img src="/icons/icon.svg" width={30} height={30} alt="" />
                    BauLV
                  </a>
                  <div className="copy" data-sc-cue="0 0.82 0 0.12">
                    <p className="h-hero" aria-hidden="true">Malerflächen aus Ihrem Plan –</p>
                    <p className="lede">Räume, Flächen und Öffnungen, direkt aus Ihrem Grundriss-PDF.</p>
                  </div>
                </div>
              </div>
            </div>
            <div className="side side--lv">
              <div className="half">
                <div className="pad">
                  <figure className="sheet sheet--far plane" data-depth="0.35">
                    <img src="/landing/lv.webp" width={1800} height={745} alt="BauLV Leistungsverzeichnis Malerarbeiten mit drei Positionen und Mengen" />
                  </figure>
                  <figure className="sheet sheet--near plane" data-depth="1">
                    <img src="/landing/nachweis.webp" width={1800} height={1342} alt="Aufgeklappte LV-Position mit Berechnungsnachweis je Raum" />
                  </figure>
                  <a className="login" href="/login">Anmelden</a>
                  <div className="copy" data-sc-cue="0 0.82 0 0.12">
                    <p className="h-hero" aria-hidden="true">Raum für Raum nachrechenbar.</p>
                    <a className="cta" href="#warteliste">{CTA}</a>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* 2 · Lesen: echte Bildschirmaufnahme der Raumtabelle, gescrubbt */}
        <section className="read" data-sc-act="scrub" data-sc-span="2.4" data-split="0:50">
          <div data-sc-stage>
            <div className="side side--plan">
              <div className="half">
                <div className="pad">
                  <figure className="sheet frame">
                    <img className="sc-stage__poster" src="/landing/rooms/001.webp" width={1280} height={985} alt="" />
                    <canvas data-sc-sequence="/landing/rooms/{iii}.webp:90:1" role="img" aria-label="Bildschirmaufnahme: die Raumtabelle der Plananalyse wird durchgescrollt" />
                  </figure>
                  <div className="copy" data-sc-cue="0.06 0.94 0.12 0.1">
                    <h2 className="h-act">33 Räume aus einem PDF.</h2>
                    <p className="small">Wo der Plan keine Zahl hergibt, schätzt BauLV nicht. Dort steht „Bitte eintragen“.</p>
                  </div>
                </div>
              </div>
            </div>
            <div className="side side--lv">
              <div className="half">
                <div className="pad">
                  <figure className="sheet sheet--far" data-sc-cue="0 0.9 0 0.1">
                    <img src="/landing/struktur.webp" width={1800} height={1319} alt="BauLV Gebäudestruktur: Gebäude, Geschoß, Wohnungen und Räume mit Öffnungen" />
                  </figure>
                  <div className="copy" data-sc-cue="0.1 0.94 0.15 0.1">
                    <p className="label">Gebäudestruktur</p>
                    <p className="lede">Geordnet nach Gebäude, Geschoß und Wohnung. Gerechnet ist noch nichts.</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* 3 · Übergabe (Peak): Maße verlassen die echte Tabellenzeile */}
        <section className="peak" data-sc-act="pin" data-sc-span="3.6" data-split="0:50" id="uebergabe">
          <div data-sc-stage data-sc-verify-state="0">
            <div className="side side--plan">
              <div className="half">
                <div className="pad">
                  <figure className="sheet frame">
                    <img src="/landing/rooms/090.webp" width={1280} height={985} alt="Raumtabelle, letzte Zeile: Wohnen, 27,32 m² Fläche, 23,00 m Umfang, 2,50 m Raumhöhe" />
                    <div className="rowmark" aria-hidden="true" />
                    <span className="chip src chip--num" id="c-umfang" style={{ left: "40.4%", top: "94.7%" }}>23,00</span>
                    <span className="chip src chip--num" id="c-hoehe" style={{ left: "47.9%", top: "94.7%" }}>2,50</span>
                  </figure>
                  <div className="openings">
                    <p className="label">Öffnungen</p>
                    <span className="chip" id="c-fenster"><small>Fenster</small> 2,00 × 2,10 m</span>
                    <span className="chip" id="c-tuer"><small>Tür</small> 0,90 × 2,00 m</span>
                  </div>
                  <div className="copy--plan" data-sc-cue="0 0.9 0 0.08">
                    <h2 className="h-act">Ein Raum aus dem Plan.</h2>
                    <p className="small">Wohnen, Wohnung 1. Die Maße stehen so in der Raumtabelle.</p>
                  </div>
                </div>
              </div>
            </div>
            <div className="side side--lv">
              <div className="half">
                <div className="pad">
                  <div className="proof" aria-label="Berechnungsnachweis für den Raum Wohnen">
                    <div className="proof__pos"><b>01.01</b><span>Wandbeschichtung Dispersion weiß, 2× Anstrich</span></div>
                    <p className="label">Berechnungsnachweis · Wohnen</p>
                    <div className="proof__line">
                      <span>Umfang × Höhe <span className="f">(Rohmaß)</span></span>
                      <span>
                        <span className="slot" id="s-umfang"><span className="v">23,000</span></span> ×{" "}
                        <span className="slot" id="s-hoehe"><span className="v">2,500</span></span> ={" "}
                        <span className="res" id="r-raw">57,500 m²</span>
                      </span>
                    </div>
                    <div className="proof__line ded">
                      <span>
                        <span className="slot" id="s-fenster"><span className="v">Fenster 2,00 × 2,10</span></span>{" "}
                        <span className="res why" id="r-fenster-why">erfasst</span>
                      </span>
                      <span className="res" id="r-fenster">4,200 m²</span>
                    </div>
                    <div className="proof__line ded">
                      <span>
                        <span className="slot" id="s-tuer"><span className="v">Tür 0,90 × 2,00</span></span>{" "}
                        <span className="res why" id="r-tuer-why">erfasst</span>
                      </span>
                      <span className="res" id="r-tuer">1,800 m²</span>
                    </div>
                    <div className="net"><span>Rohmaß Wand</span><span className="res" id="r-net">57,500 m²</span></div>
                  </div>
                  <div className="copy" data-sc-cue="0.62 0.94 0.1 0.1">
                    <p className="you">Sie sehen jeden Schritt.</p>
                    <p className="small">
                      Die KI liest den Plan. Gerechnet wird nach festen BauLV-Regeln, reproduzierbar:
                      Rohmaß und jede Öffnung stehen als eigene Zeile im Nachweis der Position.
                    </p>
                  </div>
                </div>
              </div>
            </div>
            <div className="flyers" aria-hidden="true" />
          </div>
        </section>

        {/* 4 · Prüfen: der Besucher bedient echte Beispieldaten */}
        <section className="check" data-sc-act="flow" data-split="0:50" id="pruefen" aria-labelledby="pruefen-h">
          <RoomPicker />
        </section>

        {/* 5 · Liefern */}
        <section className="ship" data-sc-act="pin" data-sc-span="1.8" data-split="0:50">
          <div data-sc-stage>
            <div className="side side--plan">
              <div className="half">
                <div className="pad">
                  <figure className="sheet" data-sc-cue="0 0.92 0 0.1">
                    <img src="/landing/projekt.webp" width={1800} height={853} alt="BauLV Projektübersicht mit Plananalyse, Leistungsverzeichnis, Gebäudestruktur und Mengenermittlung als PDF" />
                  </figure>
                  <div className="copy" data-sc-cue="0 0.92 0 0.1">
                    <p className="label">Hinein</p>
                    <h2 className="h-act">Ein Grundriss als PDF.</h2>
                  </div>
                </div>
              </div>
            </div>
            <div className="side side--lv">
              <div className="half">
                <div className="pad">
                  <figure className="sheet" data-sc-reveal="down" data-sc-reveal-at="0.12 0.55">
                    <img src="/landing/nachweis.webp" width={1800} height={1342} alt="LV-Position Wandbeschichtung mit aufgeklapptem Berechnungsnachweis" />
                  </figure>
                  <div className="copy" data-sc-cue="0.3 0.94 0.15 0.1">
                    <p className="label">Heraus</p>
                    <h2 className="h-act">Positionen mit Menge und Rechenweg.</h2>
                    <p className="small">In österreichischer Baufachsprache. Export als PDF oder Excel.</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* 6 · Entscheidung: die Fuge wandert an den Rand, das LV gewinnt */}
        <section className="close" data-sc-act="pin" data-sc-span="1.5" data-split="0:50,0.55:0">
          <div data-sc-stage>
            <div className="side side--plan">
              <div className="half">
                <div className="pad">
                  <span className="brand" aria-hidden="true">
                    <img src="/icons/icon.svg" width={30} height={30} alt="" />
                    BauLV
                  </span>
                </div>
              </div>
            </div>
            <div className="side side--lv">
              <div className="half">
                <div className="final" data-sc-cue="0 1 0 0">
                  <h2 className="h-hero">
                    Sie prüfen.
                    <br />
                    BauLV rechnet.
                  </h2>
                  <p className="lede">
                    BauLV ist in der Testphase. Sie testen kostenlos, mit allen Funktionen, und sehen den echten Stand.
                  </p>
                  <a className="cta" href="#warteliste">{CTA}</a>
                  <p className="small">Für Unternehmen (B2B). Entwickelt in Österreich.</p>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Ziel aller CTAs: bestehende Warteliste (Double-Opt-In) */}
        <section id="warteliste" className="signup" aria-labelledby="warteliste-h">
          <div className="signup__inner">
            <div className="sc-stack">
              <p className="label">{CTA}</p>
              <h2 className="h-act" id="warteliste-h">Platz in der Testphase sichern.</h2>
              <p className="lede">
                BauLV ist in der geschlossenen Testphase. Tragen Sie sich unverbindlich ein, wir melden uns per
                E-Mail, sobald Ihr Zugang freigeschaltet ist.
              </p>
              <p className="small">
                Kein Vertrag, keine Kosten, keine Zahlungsdaten. Sie bestätigen die Anmeldung per E-Mail
                (Double-Opt-In) und können sich jederzeit über den Link in jeder E-Mail abmelden.
              </p>
            </div>
            <WaitlistForm />
          </div>
        </section>
      </main>

      <footer className="lfoot">
        <div className="lfoot__inner">
          <span>© 2026 BauLV</span>
          <nav aria-label="Rechtliches und Links">
            <a href="/login">Anmelden</a>
            <a href="/api-pricing">API für Entwickler</a>
            <a href="/impressum">Impressum</a>
            <a href="/datenschutz">Datenschutz</a>
            <a href="/agb">AGB</a>
          </nav>
        </div>
      </footer>

      <SupportChat />
    </div>
  );
}
