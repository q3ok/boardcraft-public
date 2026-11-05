import { mountPlayApp } from "./public";
import "./standalone.css";
import { applyPalette } from "./ui/palette";
applyPalette();
mountPlayApp(document.getElementById("app")!);
