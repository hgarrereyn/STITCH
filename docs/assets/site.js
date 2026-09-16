// Keep old documentation bookmarks useful after removing Docsify.
const legacyPages = {
  "/": "about",
  "/README": "about",
  "/getting-started": "quickstart",
  "/architecture": "about",
  "/cli-reference": "reference",
};

function resolveLegacyLink() {
  const path = location.hash.slice(1).split("?")[0].replace(/\.md$/, "");
  const target = legacyPages[path];
  if (target) location.replace(`#${target}`);
}

resolveLegacyLink();
window.addEventListener("hashchange", resolveLegacyLink);

// Commands remain readable and selectable when scripting or copying is unavailable.
if (navigator.clipboard && window.isSecureContext) {
  document.querySelectorAll(".code-block").forEach((block) => {
    const tools = document.createElement("div");
    tools.className = "code-tools";
    const label = document.createElement("span");
    label.textContent = "Shell";
    const button = document.createElement("button");
    button.className = "copy-button";
    button.type = "button";
    button.textContent = "Copy";
    button.setAttribute("aria-label", `Copy: ${block.querySelector("pre").getAttribute("aria-label")}`);
    button.addEventListener("click", async () => {
      const status = document.getElementById("copy-status");
      try {
        await navigator.clipboard.writeText(block.querySelector("code").textContent);
        button.textContent = "Copied";
        status.textContent = "Commands copied to clipboard.";
        setTimeout(() => { button.textContent = "Copy"; }, 2000);
      } catch {
        status.textContent = "Could not copy. Select the commands and copy them manually.";
      }
    });
    tools.append(label, button);
    block.prepend(tools);
  });
}
