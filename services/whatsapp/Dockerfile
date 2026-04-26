FROM node:20-slim

WORKDIR /app

COPY package*.json ./
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN npm install --omit=dev

COPY . .

EXPOSE 3000

CMD ["node", "index.js"]
