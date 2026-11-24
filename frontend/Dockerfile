FROM node:20-slim
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# VITE_API_BASE is baked in at build time (Vite inlines import.meta.env).
# On Railway, set it as a service variable -> it's passed in as this build arg.
ARG VITE_API_BASE
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

EXPOSE 5173
# Serve the built SPA. Railway injects $PORT; fall back to 5173 locally.
CMD ["sh", "-c", "npx vite preview --host 0.0.0.0 --port ${PORT:-5173}"]
