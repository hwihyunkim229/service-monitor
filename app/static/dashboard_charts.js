(() => {
  window.dashboardShouldShowLabel = (chart, index, count, minSpacing = 58) => {
    const width = chart?.chartArea?.width || chart?.width || 0;
    if (!count || count <= 1 || width / count >= minSpacing) return true;
    const step = Math.ceil(minSpacing / Math.max(width / count, 1));
    return index === 0 || index === count - 1 || index % step === 0;
  };

  window.dashboardLabelY = (chart, y, offset = 10) =>
    Math.max(y - offset, (chart?.chartArea?.top || 0) + 11);

  const rectanglesOverlap = (a, b, gap = 3) =>
    a.x < b.x + b.width + gap &&
    a.x + a.width + gap > b.x &&
    a.y < b.y + b.height + gap &&
    a.y + a.height + gap > b.y;

  const lineYAtX = (meta, x) => {
    const points = meta?.data || [];

    for (let index = 1; index < points.length; index += 1) {
      const previous = points[index - 1];
      const current = points[index];

      if (!previous || !current || previous.skip || current.skip) continue;

      const left = Math.min(previous.x, current.x);
      const right = Math.max(previous.x, current.x);
      if (x < left || x > right) continue;

      const width = current.x - previous.x;
      if (Math.abs(width) < 0.001) return current.y;

      const ratio = (x - previous.x) / width;
      return previous.y + ((current.y - previous.y) * ratio);
    }

    return null;
  };

  const lineCrossesLabel = (meta, rect) => {
    const sampleXs = [
      rect.x,
      rect.x + (rect.width / 2),
      rect.x + rect.width
    ];

    return sampleXs.some((x) => {
      const y = lineYAtX(meta, x);
      return y !== null && y >= rect.y - 3 && y <= rect.y + rect.height + 3;
    });
  };

  const roundedRectangle = (ctx, x, y, width, height, radius) => {
    const corner = Math.min(radius, width / 2, height / 2);

    ctx.beginPath();
    ctx.moveTo(x + corner, y);
    ctx.lineTo(x + width - corner, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + corner);
    ctx.lineTo(x + width, y + height - corner);
    ctx.quadraticCurveTo(x + width, y + height, x + width - corner, y + height);
    ctx.lineTo(x + corner, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - corner);
    ctx.lineTo(x, y + corner);
    ctx.quadraticCurveTo(x, y, x + corner, y);
    ctx.closePath();
  };

  const findUptimeLabelRect = (chart, item, width, height, placedRects) => {
    const area = chart.chartArea;
    const horizontalPadding = 3;
    const preferredBelow = item.preferredBelow;
    const offsets = preferredBelow ? [10, -10, 27, -27, 44, -44] : [-10, 10, -27, 27, -44, 44];

    const candidates = offsets.map((offset, order) => {
      const below = offset > 0;
      const rawX = item.point.x - (width / 2);
      const x = Math.min(
        Math.max(rawX, area.left + horizontalPadding),
        area.right - width - horizontalPadding
      );
      const rawY = below ? item.point.y + offset : item.point.y + offset - height;
      const y = Math.min(
        Math.max(rawY, area.top + 2),
        area.bottom - height - 2
      );
      const rect = {
        x,
        y,
        width,
        height
      };
      let score = order * 5;

      placedRects.forEach((placed) => {
        if (rectanglesOverlap(rect, placed, 4)) score += 1000;
      });

      chart.data.datasets.forEach((dataset, datasetIndex) => {
        if (!chart.isDatasetVisible(datasetIndex)) return;
        const meta = chart.getDatasetMeta(datasetIndex);
        if (meta.type !== "line") return;
        if (lineCrossesLabel(meta, rect)) {
          score += datasetIndex === item.datasetIndex ? 90 : 180;
        }
      });

      if (rawY !== y) score += 300;
      return {
        rect,
        score
      };
    });

    candidates.sort((a, b) => a.score - b.score);
    return candidates[0].rect;
  };

  const drawUptimeLabel = (chart, item, rect) => {
    const {
      ctx
    } = chart;
    const labelCenterX = rect.x + (rect.width / 2);
    const labelAbovePoint = rect.y + rect.height <= item.point.y;
    const connectorY = labelAbovePoint ? rect.y + rect.height : rect.y;

    ctx.save();
    ctx.strokeStyle = item.color;
    ctx.globalAlpha = item.highContrast ? 0.62 : 0.42;
    ctx.lineWidth = item.highContrast ? 1.25 : 1;
    ctx.beginPath();
    ctx.moveTo(item.point.x, item.point.y);
    ctx.lineTo(labelCenterX, connectorY);
    ctx.stroke();
    ctx.globalAlpha = 1;

    roundedRectangle(ctx, rect.x, rect.y, rect.width, rect.height, 5);
    ctx.fillStyle = item.highContrast ? "rgba(255, 255, 255, 0.99)" : "rgba(255, 255, 255, 0.94)";
    ctx.fill();
    ctx.strokeStyle = `${item.color}${item.highContrast ? "88" : "55"}`;
    ctx.lineWidth = item.highContrast ? 1.25 : 1;
    ctx.stroke();

    ctx.font = item.font;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = item.color;
    ctx.fillText(item.text, labelCenterX, rect.y + (rect.height / 2) + 0.5);
    ctx.restore();
  };

  window.dashboardUptimeLabelPlugin = {
    id: "dashboardUptimeLabels",

    afterDatasetsDraw(chart) {
      const {
        ctx
      } = chart;
      const labelItems = [];
      const datasets = chart.data.datasets || [];
      const weeklyIndex = datasets.findIndex((dataset) => dataset.label === "주별 무중단 운영");
      const cumulativeIndex = datasets.findIndex((dataset) => dataset.label === "누적 무중단 운영");
      const weeklyMeta = weeklyIndex >= 0 ? chart.getDatasetMeta(weeklyIndex) : null;
      const cumulativeMeta = cumulativeIndex >= 0 ? chart.getDatasetMeta(cumulativeIndex) : null;

      if (weeklyIndex >= 0 && chart.isDatasetVisible(weeklyIndex)) {
        const weeklyDataset = datasets[weeklyIndex];
        weeklyMeta.data.forEach((point, index) => {
          if (!point || point.skip) return;
          if (!window.dashboardShouldShowLabel(chart, index, weeklyMeta.data.length, 54)) return;

          const value = Number(weeklyDataset.data[index]);
          if (!Number.isFinite(value) || value <= 0) return;

          const cumulativePoint = cumulativeMeta?.data?.[index];
          const closeToCumulative = cumulativePoint && Math.abs(cumulativePoint.y - point.y) < 30;

          labelItems.push({
            point,
            datasetIndex: weeklyIndex,
            text: `${value.toFixed(3)}%`,
            color: weeklyDataset.borderColor || "#0f766e",
            font: weeklyDataset.uptimeLabelFont || "600 10px sans-serif",
            highContrast: Boolean(weeklyDataset.uptimeLabelHighContrast),
            preferredBelow: Boolean(closeToCumulative && cumulativePoint.y < point.y)
          });
        });
      }

      if (cumulativeIndex >= 0 && chart.isDatasetVisible(cumulativeIndex)) {
        const cumulativeDataset = datasets[cumulativeIndex];
        const lastIndex = cumulativeDataset.data.length - 1;
        const point = cumulativeMeta?.data?.[lastIndex];
        const value = Number(cumulativeDataset.data[lastIndex]);

        if (point && !point.skip && Number.isFinite(value) && value > 0) {
          const weeklyPoint = weeklyMeta?.data?.[lastIndex];

          labelItems.push({
            point,
            datasetIndex: cumulativeIndex,
            text: `${value.toFixed(3)}%`,
            color: cumulativeDataset.borderColor || "#3b82f6",
            font: cumulativeDataset.uptimeLabelFont || "700 10px sans-serif",
            highContrast: Boolean(cumulativeDataset.uptimeLabelHighContrast),
            preferredBelow: Boolean(weeklyPoint && weeklyPoint.y < point.y)
          });
        }
      }

      const placedRects = [];
      labelItems.forEach((item) => {
        ctx.save();
        ctx.font = item.font;
        const width = Math.ceil(ctx.measureText(item.text).width) + 10;
        ctx.restore();

        const rect = findUptimeLabelRect(chart, item, width, 18, placedRects);
        placedRects.push(rect);
        drawUptimeLabel(chart, item, rect);
      });
    }
  };

  if (!window.Chart) return;

  try {
    Chart.defaults.color = '#45566d';
    Chart.defaults.font.family = "Pretendard, 'Noto Sans KR', Arial, sans-serif";
    Chart.defaults.font.size = 11;
    Chart.defaults.font.weight = '500';
    Chart.defaults.devicePixelRatio = Math.min(
      Math.max(window.devicePixelRatio || 1, 1),
      1.5
    );
    ['category', 'linear', 'logarithmic', 'time', 'timeseries', 'radialLinear'].forEach((scaleType) => {
      const scaleDefaults = Chart.defaults.scales?.[scaleType];
      if (!scaleDefaults) return;
      scaleDefaults.alignToPixels = true;
      if (scaleDefaults.ticks) {
        scaleDefaults.ticks.color = '#45566d';
      }
    });
    Chart.defaults.animation.duration = 220;
    Chart.defaults.animation.easing = 'easeOutQuart';
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.pointStyle = 'circle';
    Chart.defaults.plugins.legend.labels.boxWidth = 7;
    Chart.defaults.plugins.legend.labels.boxHeight = 7;
    Chart.defaults.plugins.legend.labels.padding = 18;
    Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(18, 31, 48, .94)';
    Chart.defaults.plugins.tooltip.titleColor = '#fff';
    Chart.defaults.plugins.tooltip.bodyColor = '#dce6ee';
    Chart.defaults.plugins.tooltip.padding = 11;
    Chart.defaults.plugins.tooltip.cornerRadius = 8;
  } catch (error) {
    console.warn('Dashboard chart defaults could not be applied.', error);
  }
})();