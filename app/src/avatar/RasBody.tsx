import { memo, type MutableRefObject } from "react";
import faces from "./faces.json";
import type { Face } from "./Helmet";

/** Ras's whole body, drawn to match the character sheet: white plates, green circuit lines, black joints, long tapered
 *  legs, a tail. Every moving part is a group registered in `parts`; RasLive animates them directly, so a moving body
 *  never re-renders React. Lengths are shared with the animation through THIGH, SHIN, UPPER and NECK. */
export const THIGH = 105, SHIN = 216, UPPER = 62, NECK = 150, HIP_X = 24;
export type Parts = Record<string, SVGElement | null>;

const INK = "#1d2118", PLATE = "#ecede2", SHADE = "#c9cbbd", GREEN = "#5b9a33", LIME = "#a6f52b", JOINT = "#14170f";
const edge = { stroke: INK, strokeWidth: 3.5, strokeLinejoin: "round" as const, strokeLinecap: "round" as const };
const line = { fill: "none", stroke: GREEN, strokeWidth: 3.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

function Thigh() {
  return <>
    <path d="M-23 0 Q-27 52 -17 105 L17 105 Q27 52 23 0 Q0 -12 -23 0Z" fill={PLATE} {...edge} />
    <path d="M23 0 Q27 52 17 105 L7 105 Q15 52 12 -5Z" fill={SHADE} opacity=".55" />
    <path d="M-14 18 L-14 56 L-2 68 L-2 96" {...line} /><path d="M2 42 L18 42" {...line} stroke={LIME} />
  </>;
}
function Shin() {
  return <>
    <path d="M-15 0 C-27 60 -47 130 -45 180 C-44 206 -20 216 0 216 C20 216 44 206 45 180 C47 130 27 60 15 0Z" fill={PLATE} {...edge} />
    <path d="M15 0 C27 60 47 130 45 180 C44 196 34 206 22 212 C30 170 18 70 6 2Z" fill={SHADE} opacity=".6" />
    <path d="M-45 176 Q0 160 45 176 C46 204 20 216 0 216 C-20 216 -46 204 -45 176Z" fill={GREEN} {...edge} />
    <path d="M-16 38 L-27 104 L-8 124 L-12 150 L18 164" {...line} /><path d="M10 134 L30 142" {...line} stroke={LIME} />
    <ellipse cx="17" cy="92" rx="4" ry="10" fill="#9b9d90" />
    <path d="M0 -15 L15 0 L0 15 L-15 0Z" fill={GREEN} {...edge} />
  </>;
}
function Hand() {
  return <g fill="none" strokeLinecap="round" strokeWidth="8">
    <path d="M-22 134 Q-34 150 -30 170" stroke={GREEN} /><path d="M-10 138 Q-16 160 -14 180" stroke={LIME} />
    <path d="M2 138 Q2 162 8 182" stroke={GREEN} /><path d="M14 136 Q24 156 28 174" stroke={LIME} /><path d="M24 128 Q42 134 48 150" stroke={GREEN} />
  </g>;
}
function Forearm() {
  return <>
    <Hand />
    <path d="M-11 0 C-20 40 -35 88 -33 122 Q0 138 33 122 C35 88 20 40 11 0 Q0 -7 -11 0Z" fill={PLATE} {...edge} />
    <path d="M11 0 C20 40 35 88 33 122 Q24 128 14 130 C22 90 12 40 3 -3Z" fill={SHADE} opacity=".55" />
    <ellipse cx="0" cy="127" rx="29" ry="9" fill={JOINT} stroke={GREEN} strokeWidth="4" />
    <circle cx="-6" cy="66" r="3" fill="#9b9d90" />
    <path d="M-30 96 Q-37 72 -20 62 M-14 70 Q-20 86 -9 104" fill="none" stroke={LIME} strokeWidth="6" strokeLinecap="round" />
  </>;
}
function Arm({ side, parts }: { side: "L" | "R"; parts: MutableRefObject<Parts> }) {
  const x = side === "L" ? -46 : 46;
  return (
    <g transform={`translate(${x} -130)`}>
      <g ref={(e) => { parts.current[`arm${side}`] = e; }}>
        <rect x="-9" y="-2" width="18" height={UPPER + 8} rx="9" fill={JOINT} {...edge} />
        <g transform={`translate(0 ${UPPER})`}>
          <g ref={(e) => { parts.current[`fore${side}`] = e; }}>
            <g transform={side === "R" ? "scale(-1 1)" : undefined}><Forearm /></g>
          </g>
        </g>
        <circle r="15" fill={PLATE} {...edge} />
      </g>
    </g>
  );
}
function Ear({ side, parts }: { side: "L" | "R"; parts: MutableRefObject<Parts> }) {
  return (
    <g transform={side === "R" ? "scale(-1 1)" : undefined}>
      <g ref={(e) => { parts.current[`ear${side}`] = e; }} style={{ transformOrigin: "-44px -122px" }}>
        <path d="M-68 -106 L-71 -163 Q-69 -172 -60 -167 L-22 -130Z" fill={PLATE} {...edge} />
        <path d="M-58 -124 L-61 -152 L-38 -132Z" fill={GREEN} />
      </g>
    </g>
  );
}

const Static = memo(function Static({ parts }: { parts: MutableRefObject<Parts> }) {
  const setRef = (k: string) => (e: SVGElement | null) => { parts.current[k] = e; };
  return <>
    <g data-part="tail">
      <path ref={setRef("tailInk")} fill="none" stroke={INK} strokeWidth="17" strokeLinecap="round" strokeLinejoin="round" />
      <path ref={setRef("tailWhite")} fill="none" stroke={PLATE} strokeWidth="10" strokeLinecap="round" strokeLinejoin="round" />
      <path ref={setRef("tailTip")} fill="none" stroke={GREEN} strokeWidth="11" strokeLinecap="round" strokeLinejoin="round" />
    </g>
    {(["L", "R"] as const).map((s) => (
      <g key={s} data-part="leg" ref={setRef(`leg${s}`)}>
        <g ref={setRef(`thigh${s}`)}>
          <g transform={s === "R" ? "scale(-1 1)" : undefined}><Thigh /></g>
          <g transform={`translate(0 ${THIGH})`}>
            <g ref={setRef(`shin${s}`)}><g transform={s === "R" ? "scale(-1 1)" : undefined}><Shin /></g></g>
          </g>
        </g>
      </g>
    ))}
    <g ref={setRef("torso")}>
      <g data-part="body">
        <path d="M-32 2 Q-40 -26 -23 -42 L23 -42 Q40 -26 32 2 Q0 20 -32 2Z" fill={PLATE} {...edge} />
        <path d="M0 -36 L0 2" {...line} stroke="#9b9d90" strokeWidth="2.5" />
        <rect x="-24" y="-68" width="48" height="32" rx="6" fill={JOINT} {...edge} />
        <path d="M-26 -64 Q-52 -100 -46 -130 Q-30 -150 0 -150 Q30 -150 46 -130 Q52 -100 26 -64 Q0 -56 -26 -64Z" fill={PLATE} {...edge} />
        <path d="M46 -130 Q52 -100 26 -64 Q18 -61 10 -60 Q34 -96 30 -146Z" fill={SHADE} opacity=".5" />
        <path d="M-34 -122 L-14 -122 L-8 -132 M34 -122 L14 -122 L8 -132 M0 -102 L0 -66 M-30 -84 L-12 -76 M30 -84 L12 -76" {...line} />
        <ellipse ref={setRef("core")} cx="0" cy="-114" rx="6" ry="10" fill={LIME} stroke={GREEN} strokeWidth="3" />
      </g>
      <Arm side="L" parts={parts} /><Arm side="R" parts={parts} />
      <path d="M-20 -150 Q0 -140 20 -150 L16 -160 L-16 -160Z" fill={GREEN} {...edge} />
      <g transform={`translate(0 -${NECK})`}>
        <g ref={setRef("head")} data-part="head">
          <g ref={setRef("antenna")} style={{ transformOrigin: "8px -134px" }}>
            <path d="M8 -134 L20 -190" fill="none" stroke={INK} strokeWidth="3.5" strokeLinecap="round" />
            <ellipse cx="21" cy="-195" rx="4" ry="7" fill={PLATE} {...edge} />
          </g>
          <Ear side="L" parts={parts} /><Ear side="R" parts={parts} />
          <path d="M-67 -118 Q-79 -70 -71 -32 Q-65 -8 -30 -4 L30 -4 Q65 -8 71 -32 Q79 -70 67 -118 Q40 -137 0 -137 Q-40 -137 -67 -118Z" fill={PLATE} {...edge} />
          <path d="M67 -118 Q79 -70 71 -32 Q65 -8 42 -5 Q60 -60 50 -128Z" fill={SHADE} opacity=".5" />
          <path d="M68 -94 a19 19 0 1 1 8 34" fill="none" stroke={GREEN} strokeWidth="9" strokeLinecap="round" />
          <rect x="-48" y="-106" width="96" height="78" rx="22" fill="#0b0d08" {...edge} />
        </g>
      </g>
    </g>
  </>;
});

/** The eyes sit on the head's screen but are a separate layer, so changing expression leaves the body alone. */
export function RasBody({ parts, face, color }: { parts: MutableRefObject<Parts>; face: Face; color: string }) {
  return (
    <g ref={(e) => { parts.current.root = e; }} data-ras="1" style={{ cursor: "grab", touchAction: "none" }} role="img" aria-label={`Ras, the full-body avatar, looking ${face}`} data-face={face}>
      <Static parts={parts} />
      <g ref={(e) => { parts.current.faceAt = e; }} pointerEvents="none">
        <g fill="none" stroke={color} strokeWidth="14" strokeLinecap="round" strokeLinejoin="round" style={{ color }} transform="scale(.34) translate(-256 -232)">
          <g ref={(e) => { parts.current.eyes = e; }}>
            <g key={face} className="ras-face" dangerouslySetInnerHTML={{ __html: faces[face] }} />
          </g>
          <path d="M240 300 L256 284 L272 300" strokeWidth="8" />
        </g>
      </g>
    </g>
  );
}
