// Tauri's beforeDevCommand: runs the Python backend and Vite together, stops both on exit.
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const backend = fileURLToPath(new URL("../../backend", import.meta.url));
const opts = { stdio: "inherit", shell: process.platform === "win32" };
const procs = [
  spawn("uv", ["run", "python", "-m", "jarvis.main"], { ...opts, cwd: backend }),
  spawn("npx", ["vite"], opts),
];
const stop = () => { procs.forEach((p) => p.kill()); process.exit(); };
procs.forEach((p) => p.on("exit", stop));
["SIGINT", "SIGTERM", "SIGHUP"].forEach((s) => process.on(s, stop));
