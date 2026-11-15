/* charts.js — CSS 柱图的读数与「只看显著」开关；柱高由服务端算好，这里只读数据 */
(function () {
  "use strict";

  var state = {
    activeColumn: null,
    onlySignificant: false
  };

  function readout(chart, column) {
    var box = chart.parentNode.querySelector(".chart-readout");
    if (!box) {
      return;
    }
    var label = column.getAttribute("data-label") || "";
    var slope = column.getAttribute("data-slope") || "";
    var r2 = column.getAttribute("data-r2") || "";
    var note = column.getAttribute("data-note") || "";
    box.textContent = label + " · 弹性 " + slope + " · R² " + r2 + " · " + note;
  }

  function bindReadout(chart) {
    var columns = chart.querySelectorAll(".chart-col");
    for (var i = 0; i < columns.length; i += 1) {
      var column = columns[i];
      column.addEventListener("click", function () {
        state.activeColumn = this;
        readout(chart, this);
      });
      column.addEventListener("focus", function () {
        readout(chart, this);
      });
      column.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          readout(chart, this);
        }
      });
    }
  }

  function bindToggle() {
    var toggle = document.querySelector("[data-chart-toggle]");
    if (!toggle) {
      return;
    }
    toggle.addEventListener("click", function () {
      state.onlySignificant = !state.onlySignificant;
      var columns = document.querySelectorAll(".chart-col");
      var hidden = 0;
      for (var i = 0; i < columns.length; i += 1) {
        var keep = columns[i].getAttribute("data-significant") === "1";
        columns[i].hidden = state.onlySignificant && !keep;
        if (columns[i].hidden) {
          hidden += 1;
        }
      }
      toggle.textContent = state.onlySignificant ? "显示全部" : "只看显著";
      var box = document.querySelector(".chart-readout");
      if (box) {
        box.textContent = state.onlySignificant
          ? "仅显示显著组，隐藏 " + hidden + " 组"
          : "";
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var charts = document.querySelectorAll(".chart");
    for (var i = 0; i < charts.length; i += 1) {
      bindReadout(charts[i]);
    }
    bindToggle();
  });
})();
