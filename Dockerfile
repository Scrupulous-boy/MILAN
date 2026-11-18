# MILAN production runtime for Google Cloud Run.
# An explicit Dockerfile prevents Cloud Build from detecting the Android
# Gradle project as the web service runtime.
FROM node:24-bookworm-slim

ENV NODE_ENV=production \
    PORT=8080 \
    FFMPEG_BIN=/usr/bin/ffmpeg \
    FFPROBE_BIN=/usr/bin/ffprobe \
    FFMPEG_PATH=/usr/bin/ffmpeg \
    FFPROBE_PATH=/usr/bin/ffprobe

WORKDIR /workspace

# Provide stable system FFmpeg binaries so npm install does not need to
# download ffmpeg-static release archives from GitHub during Cloud Build.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install the Node service dependencies independently from Android/Gradle.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

# The Node service consists of the Express backend and static frontend.
COPY . .

# Generate the real cross-browser icon files and run repository integrity checks.
RUN npm run build

EXPOSE 8080

# npm start runs the favicon prestart hook before Express starts listening.
CMD ["npm", "start"]
