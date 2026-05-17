import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


NODE_INLINE_SCRIPT_PARSE = r"""
const fs = require('fs');
const vm = require('vm');

const htmlPath = process.argv[1];
const html = fs.readFileSync(htmlPath, 'utf8');
const scripts = [];
const re = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
let match;

while ((match = re.exec(html)) !== null) {
  scripts.push({
    body: match[1],
    index: scripts.length + 1,
    startLine: html.slice(0, match.index).split(/\r?\n/).length,
  });
}

if (!scripts.length) {
  console.error('No inline scripts found in arie-backoffice.html');
  process.exit(1);
}

for (const script of scripts) {
  try {
    new vm.Script(script.body, {
      filename: `arie-backoffice.inline-${script.index}.js`,
      lineOffset: script.startLine - 1,
    });
  } catch (err) {
    console.error(err.stack || err.message);
    process.exit(1);
  }
}

const loginScript = scripts.find((script) =>
  /\b(?:async\s+)?function\s+handleLogin\s*\(/.test(script.body)
);
if (!loginScript) {
  console.error('handleLogin was not defined in parsed inline scripts');
  process.exit(1);
}

console.log(JSON.stringify({
  inlineScripts: scripts.length,
  handleLoginScript: loginScript.index,
}));
"""


def test_backoffice_inline_scripts_parse_and_define_handle_login():
    assert shutil.which("node"), "Node.js is required for back-office inline script parsing"

    result = subprocess.run(
        ["node", "-e", NODE_INLINE_SCRIPT_PARSE, str(BACKOFFICE_HTML)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
