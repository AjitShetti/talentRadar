FROM node:20-alpine AS deps
WORKDIR /app
COPY package*.json ./
# Not --omit=dev: typescript is a devDependency and `next build` needs it to
# compile the .tsx sources. The runtime stage copies only .next/standalone, so
# none of this reaches the final image anyway.
RUN npm ci || npm install

FROM node:20-alpine AS builder
WORKDIR /app

# NEXT_PUBLIC_* variables are inlined into the client bundle at build time,
# not read at runtime — so the API URL has to be known here. Setting it only
# in the runtime environment produces a build that calls localhost:8000 from
# the visitor's browser, which is the classic "works locally, 404s in
# production" failure.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
# Most PaaS hosts assign the port at runtime; Next's standalone server reads
# both of these from the environment.
ENV PORT=3000 HOSTNAME=0.0.0.0
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
