#!/usr/bin/env node
'use strict';

// Generate a coherent cross-browser icon family from the canonical MILAN
// circular brand mark. ICO entries contain real PNG payloads at each size.
const fs = require('node:fs');
const path = require('node:path');
const sharp = require('sharp');

const ROOT = path.resolve(__dirname, '..');
const FRONTEND = path.join(ROOT, 'frontend');
const ASSETS = path.join(FRONTEND, 'assets');
const SOURCE = path.join(ASSETS, 'milan-logo-circle.png');
const NAVY = { r: 5, g: 14, b: 43, alpha: 1 };
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const SIZES = [16, 32, 48, 96, 180, 192, 512];
const ICO_SIZES = [16, 32, 48, 96, 192];

function assertPng(buffer, expectedSize, label) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 24 || !buffer.subarray(0, 8).equals(PNG_SIGNATURE)) {
    throw new Error(label + ' is not a valid PNG file.');
  }
  const width = buffer.readUInt32BE(16);
  const height = buffer.readUInt32BE(20);
  if (width !== expectedSize || height !== expectedSize) {
    throw new Error(label + ' dimensions are ' + width + 'x' + height + ', expected ' + expectedSize + 'x' + expectedSize + '.');
  }
}

async function renderPng(size) {
  const padding = Math.max(1, Math.round(size * 0.14));
  const inner = size - padding * 2;
  const logo = await sharp(SOURCE)
    .rotate()
    .resize({ width: inner, height: inner, fit: 'contain' })
    .png()
    .toBuffer();

  return sharp({
    create: {
      width: size,
      height: size,
      channels: 4,
      background: NAVY
    }
  })
    .composite([{ input: logo, left: padding, top: padding }])
    .png()
    .toBuffer();
}

function buildIco(entries) {
  const headerSize = 6;
  const entrySize = 16;
  const directorySize = headerSize + entries.length * entrySize;
  const totalPayload = entries.reduce((sum, item) => sum + item.buffer.length, 0);
  const output = Buffer.alloc(directorySize + totalPayload);

  output.writeUInt16LE(0, 0); // reserved
  output.writeUInt16LE(1, 2); // icon resource
  output.writeUInt16LE(entries.length, 4);

  let offset = directorySize;
  entries.forEach((item, index) => {
    const entryOffset = headerSize + index * entrySize;
    const sizeByte = item.size === 256 ? 0 : item.size;
    output.writeUInt8(sizeByte, entryOffset);
    output.writeUInt8(sizeByte, entryOffset + 1);
    output.writeUInt8(0, entryOffset + 2); // use true-colour PNG
    output.writeUInt8(0, entryOffset + 3);
    output.writeUInt16LE(1, entryOffset + 4); // planes
    output.writeUInt16LE(32, entryOffset + 6); // bits per pixel
    output.writeUInt32LE(item.buffer.length, entryOffset + 8);
    output.writeUInt32LE(offset, entryOffset + 12);
    item.buffer.copy(output, offset);
    offset += item.buffer.length;
  });

  return output;
}

async function main() {
  if (!fs.existsSync(SOURCE)) throw new Error('Canonical MILAN logo is missing: ' + SOURCE);
  const sourceMetadata = await sharp(SOURCE).metadata();
  if (!sourceMetadata.width || !sourceMetadata.height) {
    throw new Error('Canonical MILAN logo cannot be decoded as an image.');
  }
  fs.mkdirSync(ASSETS, { recursive: true });

  const rendered = new Map();
  for (const size of SIZES) {
    const buffer = await renderPng(size);
    assertPng(buffer, size, 'Generated ' + size + 'px icon');
    rendered.set(size, buffer);
  }

  const assetFiles = [
    [16, 'favicon-16x16.png'],
    [32, 'favicon-32x32.png'],
    [48, 'favicon-48x48.png'],
    [96, 'favicon-96x96.png'],
    [180, 'favicon-180x180.png'],
    [192, 'favicon-192x192.png'],
    [512, 'favicon-512x512.png']
  ];
  for (const [size, name] of assetFiles) {
    fs.writeFileSync(path.join(ASSETS, name), rendered.get(size));
  }

  // Install-icon aliases used by existing app UI and older manifest clients.
  fs.writeFileSync(path.join(ASSETS, 'icon-192.png'), rendered.get(192));
  fs.writeFileSync(path.join(ASSETS, 'icon-512.png'), rendered.get(512));
  fs.writeFileSync(path.join(ASSETS, 'apple-touch-icon.png'), rendered.get(180));
  fs.writeFileSync(path.join(FRONTEND, 'apple-touch-icon.png'), rendered.get(180));
  fs.writeFileSync(path.join(FRONTEND, 'favicon.png'), rendered.get(48));

  const ico = buildIco(ICO_SIZES.map(size => ({ size, buffer: rendered.get(size) })));
  if (ico.readUInt16LE(0) !== 0 || ico.readUInt16LE(2) !== 1 || ico.readUInt16LE(4) !== ICO_SIZES.length) {
    throw new Error('Generated favicon.ico has an invalid icon directory header.');
  }
  fs.writeFileSync(path.join(FRONTEND, 'favicon.ico'), ico);
  fs.writeFileSync(path.join(ASSETS, 'favicon.ico'), ico);

  // Final on-disk verification prevents a wrong extension/MIME pair from shipping.
  for (const [size, name] of assetFiles) {
    assertPng(fs.readFileSync(path.join(ASSETS, name)), size, name);
  }
  for (const size of ICO_SIZES) {
    const entry = 6 + ICO_SIZES.indexOf(size) * 16;
    const width = ico.readUInt8(entry) || 256;
    const height = ico.readUInt8(entry + 1) || 256;
    if (width !== size || height !== size) throw new Error('ICO directory entry mismatch for ' + size + 'px.');
  }

  console.log('MILAN favicon assets generated and validated: 16, 32, 48, 96, 180, 192, 512px PNG + 5-size ICO.');
}

main().catch(error => {
  console.error('MILAN favicon generation failed:', error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
