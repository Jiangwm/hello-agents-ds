import { defineComponent, h } from "vue"

import Showcase from "./components/Showcase.vue"
import ProductDashboard from "./components/ProductDashboard.vue"

export const App = defineComponent({
  name: "App",
  setup() {
    const showcase = new URLSearchParams(window.location.search).get("showcase") === "1"
    return () => h(showcase ? Showcase : ProductDashboard)
  },
})
