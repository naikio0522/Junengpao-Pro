const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { appBuilderPath } = require('app-builder-bin');

const publicDir = path.resolve(__dirname, '..', 'public');
const sourcePath = path.join(publicDir, 'icon-source.png');
const pngPath = path.join(publicDir, 'icon.png');
const icoPath = path.join(publicDir, 'icon.ico');
const icnsPath = path.join(publicDir, 'icon.icns');
const pngSignature = Buffer.from('89504e470d0a1a0a', 'hex');

function verifyPng(bytes, expectedSize) {
  if (!bytes.subarray(0, 8).equals(pngSignature)) {
    throw new Error('Icon frame is not a PNG');
  }
  const width = bytes.readUInt32BE(16);
  const height = bytes.readUInt32BE(20);
  const colorType = bytes[25];
  if (width !== expectedSize || height !== expectedSize || colorType !== 6) {
    throw new Error(`Invalid ${expectedSize}px icon frame: ${width}x${height}, PNG color type ${colorType}`);
  }
}

function renderPng(size) {
  const result = spawnSync('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-i', sourcePath,
    '-vf', `scale=${size}:${size}:flags=lanczos,format=rgba`,
    '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'
  ], { encoding: null, maxBuffer: 16 * 1024 * 1024 });
  if (result.status !== 0 || !result.stdout?.length) {
    throw new Error(`ffmpeg failed at ${size}px: ${String(result.stderr || result.error || '')}`);
  }
  verifyPng(result.stdout, size);
  return result.stdout;
}

function buildIco(frames) {
  const header = Buffer.alloc(6 + frames.length * 16);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(frames.length, 4);
  let offset = header.length;
  frames.forEach(({ size, png }, index) => {
    const entry = 6 + index * 16;
    header[entry] = size === 256 ? 0 : size;
    header[entry + 1] = size === 256 ? 0 : size;
    header.writeUInt16LE(1, entry + 4);
    header.writeUInt16LE(32, entry + 6);
    header.writeUInt32LE(png.length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += png.length;
  });
  return Buffer.concat([header, ...frames.map((frame) => frame.png)]);
}

if (!fs.existsSync(sourcePath)) {
  throw new Error(`Missing approved logo source: ${sourcePath}`);
}

const sizes = [16, 24, 32, 48, 64, 128, 256, 1024];
const images = new Map(sizes.map((size) => [size, renderPng(size)]));
fs.writeFileSync(pngPath, images.get(1024));
fs.writeFileSync(icoPath, buildIco(
  [16, 24, 32, 48, 64, 128, 256].map((size) => ({ size, png: images.get(size) }))
));
const macIcon = spawnSync(appBuilderPath, [
  'icon', '--format=icns', `--out=${publicDir}`, `--input=${pngPath}`, `--root=${publicDir}`
], { encoding: 'utf8' });
if (macIcon.status !== 0 || !fs.existsSync(icnsPath)) {
  throw new Error(`ICNS conversion failed: ${macIcon.stderr || macIcon.error || macIcon.stdout}`);
}
const icns = fs.readFileSync(icnsPath);
if (icns.toString('ascii', 0, 4) !== 'icns' || icns.readUInt32BE(4) !== icns.length) {
  throw new Error('ICNS converter produced an invalid file');
}
console.log(`Generated ${pngPath}, ${icoPath}, and ${icnsPath}`);
