/* app.js — 表单提交与失败反馈；状态集中在 state 里，不散落全局 */
(function () {
  "use strict";

  var state = {
    busy: false,
    lastError: "",
    reloadOnSuccess: true
  };

  function note(form, kind, text) {
    var box = form.querySelector(".form-note");
    if (!box) {
      box = document.createElement("p");
      box.className = "form-note";
      form.appendChild(box);
    }
    box.className = "form-note" + (kind ? " is-" + kind : "");
    box.textContent = text;
    box.hidden = false;
  }

  function payloadOf(form) {
    var out = {};
    var fields = form.querySelectorAll("[name]");
    for (var i = 0; i < fields.length; i += 1) {
      out[fields[i].name] = fields[i].value;
    }
    return out;
  }

  function submit(form) {
    var url = form.getAttribute("data-endpoint");
    if (!url || state.busy) {
      return;
    }
    state.busy = true;
    var button = form.querySelector("button[type=submit]");
    if (button) {
      button.disabled = true;
    }
    note(form, "", "运行中…");

    fetch(url, {
      method: form.getAttribute("data-method") || "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloadOf(form))
    })
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, status: response.status, body: body };
        });
      })
      .then(function (result) {
        state.busy = false;
        if (button) {
          button.disabled = false;
        }
        if (!result.ok) {
          state.lastError = result.body.message || "请求失败";
          note(form, "error", result.status + " · " + state.lastError);
          return;
        }
        var bits = [];
        if (result.body.batch_id) {
          bits.push(result.body.batch_id);
        }
        if (result.body.replay) {
          bits.push("复用已有结果");
        }
        if (result.body.counters && result.body.counters.lift_gmv) {
          bits.push("增量 " + result.body.counters.lift_gmv + " 元");
        }
        if (result.body.price_incl) {
          bits.push("不含税 " + result.body.price_incl);
        }
        if (result.body.price_excl) {
          bits.push("折后 " + result.body.price_excl);
        }
        if (result.body.notes && result.body.notes.length) {
          bits.push(result.body.notes.join(" "));
        }
        note(form, "ok", bits.join(" · ") || "完成");
        if (form.getAttribute("data-reload") !== "0" && state.reloadOnSuccess) {
          window.setTimeout(function () {
            var target = form.getAttribute("data-redirect");
            if (target) {
              window.location.href = target;
            } else {
              window.location.reload();
            }
          }, 420);
        }
      })
      .catch(function (error) {
        state.busy = false;
        if (button) {
          button.disabled = false;
        }
        note(form, "error", "网络不可用 · " + error.message);
      });
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.matches("form[data-endpoint]")) {
      return;
    }
    event.preventDefault();
    submit(form);
  });

  document.addEventListener("change", function (event) {
    var picker = event.target;
    if (!picker.matches("[data-batch-picker]")) {
      return;
    }
    var url = new URL(window.location.href);
    if (picker.value) {
      url.searchParams.set("batch_id", picker.value);
    } else {
      url.searchParams.delete("batch_id");
    }
    window.location.href = url.toString();
  });
})();
