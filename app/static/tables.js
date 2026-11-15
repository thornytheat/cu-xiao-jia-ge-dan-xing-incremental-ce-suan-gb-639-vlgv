/* tables.js — 表头排序与级别筛选；只做展示层处理，不改任何判定 */
(function () {
  "use strict";

  var state = {
    sortKey: null,
    sortAsc: true
  };

  function cellValue(row, index) {
    var cell = row.children[index];
    return cell ? cell.getAttribute("data-sort") || cell.textContent.trim() : "";
  }

  function compare(a, b) {
    var left = parseFloat(a.replace(/[^0-9.\-]/g, ""));
    var right = parseFloat(b.replace(/[^0-9.\-]/g, ""));
    var bothNumeric = !isNaN(left) && !isNaN(right) && a !== "" && b !== "";
    if (bothNumeric) {
      return left - right;
    }
    return a.localeCompare(b, "zh-Hans-CN");
  }

  function sortTable(table, index) {
    var body = table.tBodies[0];
    if (!body) {
      return;
    }
    var rows = Array.prototype.slice.call(body.rows);
    var asc = state.sortKey === index ? !state.sortAsc : true;
    state.sortKey = index;
    state.sortAsc = asc;
    rows.sort(function (a, b) {
      var result = compare(cellValue(a, index), cellValue(b, index));
      return asc ? result : -result;
    });
    for (var i = 0; i < rows.length; i += 1) {
      body.appendChild(rows[i]);
    }
  }

  function bindSort(table) {
    var heads = table.querySelectorAll("thead th");
    for (var i = 0; i < heads.length; i += 1) {
      heads[i].setAttribute("role", "columnheader");
      heads[i].tabIndex = 0;
      heads[i].style.cursor = "pointer";
      (function (index) {
        function run() {
          sortTable(table, index);
        }
        heads[index].addEventListener("click", run);
        heads[index].addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            run();
          }
        });
      })(i);
    }
  }

  function bindFilter() {
    var select = document.querySelector("[data-table-filter]");
    if (!select) {
      return;
    }
    var table = document.querySelector(select.getAttribute("data-table-filter"));
    if (!table) {
      return;
    }
    select.addEventListener("change", function () {
      var wanted = select.value;
      var rows = table.tBodies[0] ? table.tBodies[0].rows : [];
      var shown = 0;
      for (var i = 0; i < rows.length; i += 1) {
        var keep = !wanted || rows[i].getAttribute("data-level") === wanted;
        rows[i].hidden = !keep;
        if (keep) {
          shown += 1;
        }
      }
      var counter = document.querySelector("[data-table-count]");
      if (counter) {
        counter.textContent = shown + " 条";
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var tables = document.querySelectorAll("table.grid[data-sortable]");
    for (var i = 0; i < tables.length; i += 1) {
      bindSort(tables[i]);
    }
    bindFilter();
  });
})();
