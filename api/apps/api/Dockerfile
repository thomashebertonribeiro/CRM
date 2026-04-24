FROM node:22-alpine

# Install build tools needed for better-sqlite3 (native addon)
RUN apk add --no-cache python3 make g++

WORKDIR /app

# Copy package files and install dependencies
COPY package.json ./
RUN npm install --omit=dev

# Copy source code
COPY src ./src

# Create data directory for RF database
RUN mkdir -p /app/data/rf

EXPOSE 3001

CMD ["node", "src/apps/api/main.js"]
