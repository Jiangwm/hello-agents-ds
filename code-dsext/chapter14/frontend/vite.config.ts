import { defineConfig } from "vite"
import vue from "@vitejs/plugin-vue"

export const config = defineConfig({
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    port: 5174,
  },
})

export default config
