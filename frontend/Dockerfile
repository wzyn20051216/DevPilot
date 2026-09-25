FROM node:22-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
ARG VITE_DEFAULT_REPO_PATH=/workspace/devpilot-test-repo
ENV VITE_DEFAULT_REPO_PATH=${VITE_DEFAULT_REPO_PATH}
RUN npm run build


FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
