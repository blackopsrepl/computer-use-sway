const pyproject = {
  filename: "pyproject.toml",
  updater: {
    readVersion: (contents) => contents.match(/^version = "(.*)"$/m)[1],
    writeVersion: (contents, version) =>
      contents.replace(/^version = ".*"$/m, `version = "${version}"`),
  },
};

const serverVersion = {
  filename: "src/computer_use_sway/version.py",
  updater: {
    readVersion: (contents) => contents.match(/^SERVER_VERSION = "(.*)"$/m)[1],
    writeVersion: (contents, version) =>
      contents.replace(/^SERVER_VERSION = ".*"$/m, `SERVER_VERSION = "${version}"`),
  },
};

// Tooling-only releases are real releases. `build` and `docs` are hidden types
// in the conventional-commits preset, so a release that ships no behaviour would
// otherwise publish an empty changelog section — and an empty section is
// indistinguishable from a release that shipped nothing. Everything else keeps
// the preset's default visibility. Kept identical to the seshat repository's
// config: the two are one pipeline and should not diverge in how they report.
const types = [
  { type: "feat", section: "Features" },
  { type: "fix", section: "Bug Fixes" },
  { type: "perf", section: "Performance Improvements" },
  { type: "revert", section: "Reverts" },
  { type: "build", section: "Build System" },
  { type: "docs", section: "Documentation" },
  { type: "style", section: "Styles", hidden: true },
  { type: "chore", section: "Miscellaneous Chores", hidden: true },
  { type: "refactor", section: "Code Refactoring", hidden: true },
  { type: "test", section: "Tests", hidden: true },
  { type: "ci", section: "Continuous Integration", hidden: true },
];

module.exports = {
  types,
  packageFiles: [pyproject],
  bumpFiles: [pyproject, serverVersion],
  tagPrefix: "v",
  releaseCommitMessageFormat: "chore(release): {{currentTag}}",
  commitUrlFormat:
    "https://github.com/blackopsrepl/computer-use-sway/commit/{{hash}}",
  compareUrlFormat:
    "https://github.com/blackopsrepl/computer-use-sway/compare/{{previousTag}}...{{currentTag}}",
};
