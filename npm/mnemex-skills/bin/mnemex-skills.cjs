#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const target = process.argv[2] || path.join(process.cwd(), ".agents", "skills", "mnemex");
const source = path.join(__dirname, "..", "SKILL.md");

fs.mkdirSync(target, { recursive: true });
fs.copyFileSync(source, path.join(target, "SKILL.md"));
process.stdout.write(`${path.join(target, "SKILL.md")}\n`);
