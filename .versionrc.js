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

module.exports = {
  packageFiles: [pyproject],
  bumpFiles: [pyproject, serverVersion],
  tagPrefix: "v",
  releaseCommitMessageFormat: "chore(release): {{currentTag}}",
  commitUrlFormat:
    "https://github.com/blackopsrepl/computer-use-sway/commit/{{hash}}",
  compareUrlFormat:
    "https://github.com/blackopsrepl/computer-use-sway/compare/{{previousTag}}...{{currentTag}}",
};
