// Solves the sketches in the JSON file named by argv[2] with the viewer's own
// solver (vendored planegcs, WebAssembly) and prints the answers as JSON.
// test_solver_parity.py compares them with sprocket_engine.solver.
const fs = require("fs");
const path = require("path");
const viewer = path.resolve(__dirname, "../../viewer");
require(path.join(viewer, "vendor/planegcs.js"));
const { solve } = require(path.join(viewer, "sketch-solver.js"));

(async () => {
  const cases = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
  const answers = {};
  for (const [name, { sketch, drag }] of Object.entries(cases)) {
    // A new solver per case. The viewer keeps one for its whole life, and that
    // one answers a little differently after it has solved other sketches: a
    // dimensioned slot moved by 6e-6 mm once five other cases had run on it.
    // That is far below what a person can see, but it would make this
    // comparison depend on the order of the cases.
    const gcs = await globalThis.Planegcs.createSolver();
    answers[name] = { solve: solve(gcs, sketch), drag: drag ? solve(gcs, sketch, { drag }) : null };
  }
  // planegcs prints diagnostics on stdout, so the answers go to a marked line.
  console.log("ANSWERS " + JSON.stringify(answers));
})();
