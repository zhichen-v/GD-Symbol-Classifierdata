const loginForm = document.querySelector("#login-form");
const passwordInput = document.querySelector("#password-input");
const loginButton = document.querySelector("#login-button");
const loginMessage = document.querySelector("#login-message");

async function checkExistingSession() {
  try {
    const response = await fetch("/api/auth", { credentials: "same-origin" });
    const payload = await response.json();
    if (!payload.enabled || payload.authenticated) {
      window.location.replace("/");
    }
  } catch {
    loginMessage.textContent = "無法連線到服務";
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginMessage.textContent = "";
  loginButton.disabled = true;
  loginButton.textContent = "確認中";

  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ password: passwordInput.value }),
    });
    if (!response.ok) {
      loginMessage.textContent = "密碼不正確";
      return;
    }
    window.location.replace("/");
  } catch {
    loginMessage.textContent = "無法連線到服務";
  } finally {
    loginButton.disabled = false;
    loginButton.textContent = "進入";
  }
});

checkExistingSession();
