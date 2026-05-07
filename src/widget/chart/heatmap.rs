mod instance;
mod scene;
mod ui;
mod view;
mod widget;

use instance::InstanceBuilder;
use scene::{
    Scene,
    cell::Cell,
    depth_grid::{GridRing, HeatmapPalette},
};
use ui::CanvasCaches;
use ui::axisx::AxisXLabelCanvas;
use ui::axisy::AxisYLabelCanvas;
use ui::overlay::OverlayCanvas;
use view::{ViewConfig, ViewInputs, ViewWindow};
use widget::{DEFAULT_Y_AXIS_GUTTER, HeatmapShaderWidget};

use crate::{
    chart::Action,
    modal::pane::settings::study::{self, Study},
};
use data::aggr::time::TimeSeries;
use data::chart::{
    Basis,
    heatmap::{HeatmapDataPoint, HeatmapStudy, HistoricalDepth},
    indicator::HeatmapIndicator,
};
use exchange::depth::Depth;
use exchange::unit::{Price, PriceStep};
use exchange::{TickerInfo, Trade};

use std::time::{Duration, Instant};

// Volume strip
const STRIP_HEIGHT_FRAC: f32 = 0.10;

// Debounce heavy CPU rebuilds (notably `rebuild_from_historical`) during interaction
const REBUILD_DEBOUNCE_MS: u64 = 250;

// If rendering stalls longer than this, assume GPU heatmap texture may have been lost/desynced
const HEATMAP_RESYNC_AFTER_STALL_MS: u64 = 750;

// Volume profile width as % of viewport width
const VOLUME_PROFILE_WIDTH_PCT: f32 = 0.10;

// Depth profile width in pixels fixed
const DEPTH_PROFILE_WIDTH_PX: f32 = 160.0;

#[derive(Debug, Clone, PartialEq, Eq)]
enum RebuildDecision {
    Idle,
    OverlaysOnly,
    Full,
}

#[derive(Debug, Clone, Default)]
enum RebuildSignal {
    #[default]
    Idle,
    /// `since` は常に過去の時刻（デバウンス開始時刻）。
    /// `decide()` が `saturating_duration_since` を使うため、将来時刻が誤って渡されても
    /// 経過 0ms として扱われ、ハングしない（実装上の安全装置）。
    Debouncing {
        since: Instant,
        force_historical: bool,
    },
    Immediate {
        force_historical: bool,
    },
}

impl RebuildSignal {
    fn is_interacting(&self) -> bool {
        matches!(self, Self::Debouncing { .. })
    }

    fn set_debouncing(&mut self, force_historical: bool) {
        let prev = self.peek_force_historical();
        *self = Self::Debouncing {
            since: Instant::now(),
            force_historical: prev || force_historical,
        };
    }

    // Schedule an immediate full rebuild, bypassing the debounce window.
    // `force_historical` is OR-merged with any existing flag so a true value is never silently
    // downgraded. Callers must ensure `set_idle()` is called after the rebuild completes.
    fn set_immediate(&mut self, force_historical: bool) {
        let prev = self.peek_force_historical();
        *self = Self::Immediate {
            force_historical: prev || force_historical,
        };
    }

    fn set_idle(&mut self) {
        *self = Self::Idle;
    }

    fn peek_force_historical(&self) -> bool {
        match self {
            Self::Immediate { force_historical }
            | Self::Debouncing {
                force_historical, ..
            } => *force_historical,
            Self::Idle => false,
        }
    }

    fn take_force_historical(&mut self) -> bool {
        match self {
            Self::Immediate { force_historical }
            | Self::Debouncing {
                force_historical, ..
            } => std::mem::replace(force_historical, false),
            Self::Idle => false,
        }
    }

    fn decide(&self, now: Instant) -> RebuildDecision {
        match self {
            Self::Idle => RebuildDecision::Idle,
            Self::Immediate { .. } => RebuildDecision::Full,
            Self::Debouncing { since, .. } => {
                let elapsed = now.saturating_duration_since(*since).as_millis() as u64;
                if elapsed >= REBUILD_DEBOUNCE_MS {
                    RebuildDecision::Full
                } else {
                    RebuildDecision::OverlaysOnly
                }
            }
        }
    }
}

#[derive(Debug, Clone)]
pub enum Message {
    BoundsChanged(iced::Rectangle),
    PanDeltaPx(iced::Vector),
    ZoomAt {
        factor: f32,
        cursor: iced::Point,
    },
    ScrolledAxisY {
        factor: f32,
        cursor_y: f32,
        viewport_h: f32,
    },
    AxisYDoubleClicked,
    AxisXDoubleClicked,
    ScrolledAxisX {
        factor: f32,
        cursor_x: f32,
        viewport_w: f32,
    },
    DragZoomAxisXKeepAnchor {
        factor: f32,
        anchor_screen_x: f32,
        viewport_w: f32,
    },
    CursorMoved,
    PauseBtnClicked,
}

#[derive(Debug, Clone, Copy)]
pub struct HeatmapViewState {
    pub camera_scale: f32,
    /// GPU 内部形式の生配列 [x, y]。`view_state()` / `apply_view_state()` が変換境界を担う。
    /// クレート外部から直接アクセス不可（不透明ハンドルとして扱う）。
    pub(crate) camera_offset: [f32; 2],
    pub cell_width_world: f32,
    pub cell_height_world: f32,
}

pub struct HeatmapShader {
    pub last_tick: Option<Instant>,
    scene: Scene,
    viewport: Option<iced::Rectangle>,
    palette: Option<HeatmapPalette>,
    instances: InstanceBuilder,
    canvas_caches: CanvasCaches,

    step: PriceStep,
    pub basis: Basis,
    pub ticker_info: TickerInfo,
    pub config: data::chart::heatmap::Config,
    trades: TimeSeries<HeatmapDataPoint>,
    depth_history: HistoricalDepth,

    latest_time: Option<u64>,
    base_price: Option<Price>,
    clock: view::ExchangeClock,
    anchor: view::Anchor,
    y_axis_gutter: iced::Length,

    depth_grid: GridRing,
    depth_norm: view::DepthNormCache,
    data_gen: u64,
    qty_scale: f32,
    rebuild_signal: RebuildSignal,
    indicators: Vec<HeatmapIndicator>,
    pub studies: Vec<HeatmapStudy>,
    pub study_configurator: study::Configurator<HeatmapStudy>,
}

impl HeatmapShader {
    pub fn new(
        basis: Basis,
        step: PriceStep,
        ticker_info: TickerInfo,
        studies: Vec<HeatmapStudy>,
        indicators: Vec<HeatmapIndicator>,
    ) -> Self {
        let depth_history = HistoricalDepth::new(ticker_info.min_qty, step, basis);
        let trades = TimeSeries::<HeatmapDataPoint>::new(basis, step);

        let qty_scale: f32 = match exchange::unit::qty::volume_size_unit() {
            exchange::SizeUnit::Base => {
                let min_qty_f: f32 = ticker_info.min_qty.into();
                1.0 / min_qty_f
            }
            exchange::SizeUnit::Quote => 1.0,
        };

        Self {
            last_tick: None,
            scene: Scene::new(),
            viewport: None,
            palette: None,
            qty_scale,
            depth_history,
            step,
            basis,
            ticker_info,
            trades,
            latest_time: None,
            base_price: None,
            clock: view::ExchangeClock::Uninit,
            y_axis_gutter: DEFAULT_Y_AXIS_GUTTER,
            instances: InstanceBuilder::default(),
            canvas_caches: CanvasCaches::default(),
            depth_grid: GridRing::default(),
            depth_norm: view::DepthNormCache::new(),
            data_gen: 1,
            rebuild_signal: RebuildSignal::default(),
            indicators,
            anchor: view::Anchor::default(),
            config: data::chart::heatmap::Config::default(),
            studies,
            study_configurator: study::Configurator::new(),
        }
    }

    /// Canvas-local インタラクションを処理する。follow/pause/live 遷移は
    /// `anchor` が内部管理するが、Elm 上位への通知は `view()` が `anchor.is_paused()`
    /// を直接参照することで実現する（Effect を返さない設計は意図的）。
    pub fn update(&mut self, message: Message) {
        match message {
            Message::BoundsChanged(bounds) => {
                self.viewport = Some(bounds);
                self.clear_all_caches();
                self.rebuild_all_immediate(None, false);
            }
            Message::DragZoomAxisXKeepAnchor {
                factor,
                anchor_screen_x,
                viewport_w,
            } => {
                self.scene
                    .zoom_column_world_keep_anchor(factor, 0.0, anchor_screen_x, viewport_w);
                self.clear_x_axis_motion();

                let resumed = self.try_resume_if_x0_visible();
                self.try_rebuild_instances();
                if !resumed {
                    self.rebuild_signal.set_debouncing(false);
                }
            }
            Message::PanDeltaPx(delta_px) => {
                let cam_scale = self.scene.camera.scale();

                let dx_world = delta_px.x / cam_scale;
                let dy_world = delta_px.y / cam_scale;

                self.scene.camera.offset[0] -= dx_world;
                self.scene.camera.offset[1] -= dy_world;
                self.clear_axes_motion();

                let resumed = self.try_resume_if_x0_visible();
                self.try_rebuild_instances();
                if !resumed {
                    self.rebuild_signal.set_debouncing(false);
                }
            }
            Message::ZoomAt { factor, cursor } => {
                let Some(size) = self.viewport_size_px() else {
                    return;
                };

                let current_scale = self.scene.camera.scale();
                let desired_scale = current_scale * factor;

                let Some((min_scale, max_scale)) = self.scene.cell.camera_scale_bounds_for_pixels()
                else {
                    return;
                };

                let target_scale = desired_scale.clamp(min_scale, max_scale);

                self.scene.camera.zoom_at_cursor_to_scale(
                    target_scale,
                    cursor.x,
                    cursor.y,
                    size.width,
                    size.height,
                );
                self.clear_axes_motion();

                let resumed = self.try_resume_if_x0_visible();
                self.try_rebuild_instances();
                self.force_rebuild_if_ybin_changed();

                if !resumed {
                    self.rebuild_signal.set_debouncing(false);
                }
            }
            Message::ScrolledAxisY {
                factor,
                cursor_y,
                viewport_h,
            } => {
                self.scene.zoom_row_h_at(factor, cursor_y, viewport_h);
                self.clear_y_axis_motion();

                self.try_rebuild_instances();
                self.force_rebuild_if_ybin_changed();

                self.rebuild_signal.set_debouncing(false);
            }
            Message::ScrolledAxisX {
                factor,
                cursor_x,
                viewport_w,
            } => {
                self.scene
                    .zoom_column_world_at(factor, cursor_x, viewport_w);
                self.clear_x_axis_motion();

                let resumed = self.try_resume_if_x0_visible();
                self.try_rebuild_instances();
                if !resumed {
                    self.rebuild_signal.set_debouncing(false);
                }
            }
            Message::AxisYDoubleClicked => {
                self.scene.camera.offset[1] = 0.0;
                self.clear_y_axis_motion();

                self.try_rebuild_instances();
                self.force_rebuild_if_ybin_changed();

                self.rebuild_signal.set_debouncing(false);
            }
            Message::AxisXDoubleClicked => {
                if let Some(size) = self.viewport_size_px() {
                    self.scene
                        .camera
                        .reset_to_live_edge(size.width, false, false);
                    self.scene.set_default_column_width();
                    self.clear_x_axis_motion();

                    let resumed = self.try_resume_if_x0_visible();
                    self.try_rebuild_instances();
                    if !resumed {
                        self.rebuild_signal.set_debouncing(false);
                    }
                }
            }
            Message::PauseBtnClicked => {
                if let Some(size) = self.viewport_size_px() {
                    self.scene.camera.reset_to_live_edge(size.width, true, true);
                    self.clear_x_axis_motion();

                    let resumed = self.try_resume_if_x0_visible();

                    self.try_rebuild_instances();

                    if !resumed {
                        self.rebuild_signal.set_debouncing(false);
                    }
                }
            }
            Message::CursorMoved => {
                self.clear_cursor_moved(self.anchor.is_paused());
            }
        }
    }

    pub fn view(&self, timezone: data::UserTimezone) -> iced::Element<'_, Message> {
        if self.base_price.is_none() {
            return iced::widget::center(iced::widget::text("Waiting for data...").size(16)).into();
        }

        let render_latest_time = self.anchor.render_latest_time();
        let scroll_ref_bucket = self.anchor.scroll_ref_bucket();
        let is_paused = self.anchor.is_paused();

        let aggr_time = self.depth_history.aggr_time.max(1);
        let latest_bucket = (render_latest_time / aggr_time) as i64;
        let render_base_price = self.anchor.effective_base_price(self.base_price);

        let overlay_geometry = self.viewport.and_then(|vp| {
            view::OverlayGeometry::compute(
                &self.scene.camera,
                vp.size(),
                view::OverlayGeometryConfig {
                    depth_profile_col_width_px: DEPTH_PROFILE_WIDTH_PX,
                    volume_strip_height_pct: STRIP_HEIGHT_FRAC,
                    volume_profile_width_pct: VOLUME_PROFILE_WIDTH_PCT,
                },
            )
        });

        let x_axis = AxisXLabelCanvas {
            cache: &self.canvas_caches.x_axis,
            camera: &self.scene.camera,
            timezone,
            plot_bounds: self.viewport,
            is_paused,
            latest_bucket,
            aggr_time,
            column_world: self.scene.cell.width_world(),
            x_phase_bucket: self.anchor.x_phase_bucket(),
            is_x0_visible: self
                .viewport
                .map(|vp| self.scene.profile_start_visible_x0(vp.size())),
        };
        let y_axis = AxisYLabelCanvas {
            cache: &self.canvas_caches.y_axis,
            plot_bounds: self.viewport,
            is_paused,
            camera: &self.scene.camera,
            base_price: render_base_price,
            step: self.step,
            row_h: self.scene.cell.height_world(),
            label_precision: self.ticker_info.min_ticksize,
        };

        let overlay = OverlayCanvas {
            scene: &self.scene,
            depth_grid: &self.depth_grid,
            base_price: render_base_price,
            step: self.step,
            scroll_ref_bucket,
            qty_scale: self.qty_scale,
            tooltip_cache: &self.canvas_caches.overlay,
            scale_labels_cache: &self.canvas_caches.scale_labels,
            geometry: overlay_geometry,
            is_paused,
            volume_strip_max_qty: self.instances.volume_strip_scale_max_qty,
            depth_profile_max_qty: self.instances.depth_profile_scale_max_qty,
            volume_profile_max_qty: self.instances.volume_profile_scale_max_qty,
        };

        let chart = HeatmapShaderWidget::new(&self.scene, x_axis, y_axis, overlay)
            .with_y_axis_gutter(self.y_axis_gutter);

        iced::widget::container(chart).padding(1).into()
    }

    pub fn update_theme(&mut self, theme: &iced_core::Theme) {
        let palette = HeatmapPalette::from_theme(theme);
        self.palette = Some(palette);

        self.scene.sync_palette(self.palette.as_ref());
        self.clear_all_caches();
    }

    pub fn tick_size(&self) -> PriceStep {
        self.step
    }

    pub fn camera_scale(&self) -> f32 {
        self.scene.camera.scale()
    }

    pub fn view_state(&self) -> HeatmapViewState {
        HeatmapViewState {
            camera_scale: self.scene.camera.scale(),
            camera_offset: self.scene.camera.offset,
            cell_width_world: self.scene.cell.width_world(),
            cell_height_world: self.scene.cell.height_world(),
        }
    }

    /// Restore camera and cell dimensions from a previously captured `HeatmapViewState`.
    /// Must be called immediately after [`HeatmapShader::new`], before the first `insert_depth`.
    /// The rebuild is deferred: `rebuild_signal` is set to `Immediate { force_historical: true }`
    /// and the actual rebuild runs on the first `invalidate` once viewport and data are available.
    pub fn apply_view_state(&mut self, vs: HeatmapViewState) {
        debug_assert!(
            self.latest_time.is_none(),
            "apply_view_state called after data ingestion — will trigger a full rebuild"
        );
        self.scene.camera.set_scale(vs.camera_scale);
        self.scene.camera.offset = vs.camera_offset;
        self.scene.cell = Cell::new(vs.cell_width_world, vs.cell_height_world);
        // Do not call rebuild_all_immediate here: viewport is None at construction time,
        // so rebuild_all would early-return and destroy the force_historical flag.
        self.rebuild_signal.set_immediate(true);
    }

    /// called periodically on every window frame
    /// to update time-based rendering and animate/scroll
    pub fn invalidate(&mut self, now: Option<Instant>) -> Option<Action> {
        let now_i = now.unwrap_or_else(Instant::now);
        // Check for stall before updating last_tick so the elapsed time reflects the actual gap.
        // This handles paused mode where insert_depth never fires the stall check.
        self.mark_needs_full_upload_if_stalled();
        self.last_tick = Some(now_i);

        if self.palette.is_none() {
            return Some(Action::RequestPalette);
        }
        let viewport_size = self.viewport_size_px()?;

        if let Some(exchange_now_ms) = self.clock.estimate_now_ms(now_i) {
            let aggr_time = self.depth_history.aggr_time.max(1);
            let x0_visible = self.scene.profile_start_visible_x0(viewport_size);

            let state_change = self.anchor.tick_live_and_auto_follow(
                exchange_now_ms,
                aggr_time,
                x0_visible,
                self.base_price,
            );

            self.auto_update_anchor(state_change);

            if let Some(w) = self.compute_view_window(viewport_size) {
                self.invalidate_with_view_window(now_i, aggr_time, &w);
            }

            if !self.anchor.is_paused() {
                self.clear_x_axis();
                self.clear_overlay();
            }
        }

        None
    }

    pub fn insert_depth(&mut self, depth: &Depth, update_t: u64) {
        self.mark_needs_full_upload_if_stalled();
        let prev_effective_base = self.anchor.effective_base_price(self.base_price);

        let paused = self.anchor.is_paused();
        let is_interacting = self.rebuild_signal.is_interacting();

        let aggr_time = self.depth_history.aggr_time.max(1);
        let rounded_t = view::round_time_to_bucket(update_t, aggr_time);

        if let Some(mid) = depth.mid_price() {
            let mid_rounded = mid.round_to_step(self.step);
            self.anchor
                .apply_mid_price(mid_rounded, &mut self.base_price);
        }

        self.latest_time = Some(rounded_t);

        self.clock = self.clock.anchor_with_update(update_t);

        self.anchor
            .set_scroll_ref_bucket_if_zero((rounded_t / aggr_time) as i64);

        self.depth_grid.ensure_layout(aggr_time);
        self.depth_history.insert_latest_depth(depth, rounded_t);

        if !paused {
            self.update_live_ring_and_scene(depth, rounded_t, is_interacting);
        }

        self.data_gen = self.data_gen.wrapping_add(1);

        if (self.data_gen & 0x3F) == 0 {
            self.cleanup_old_data(aggr_time);
        }

        // is_interacting is captured before update_live_ring_and_scene, which may call
        // set_immediate(true) for recentering. set_immediate here preserves any force_historical
        // already set, so calling it again is idempotent and safe.
        if !paused && !is_interacting {
            self.rebuild_signal.set_immediate(false);
        }

        if self.anchor.effective_base_price(self.base_price) != prev_effective_base {
            self.refresh_y_axis_gutter();
            self.clear_y_axis();
        }
    }

    pub fn insert_trades(&mut self, buffer: &[Trade], update_t: u64) {
        if buffer.is_empty() {
            return;
        }

        let aggr_time = self.depth_history.aggr_time.max(1);
        let rounded_t = view::round_time_to_bucket(update_t, aggr_time);

        self.trades
            .ingest_trades_bucket(rounded_t, buffer, self.step);

        self.clear_overlay();
        self.clear_scale_labels();
        self.try_rebuild_instances();
    }

    pub fn visual_config(&self) -> data::chart::heatmap::Config {
        self.config
    }

    pub fn study_configurator(&self) -> &study::Configurator<HeatmapStudy> {
        &self.study_configurator
    }

    pub fn update_study_configurator(&mut self, message: study::Message<HeatmapStudy>) {
        let studies = &mut self.studies;
        let mut studies_changed = false;

        match self.study_configurator.update(message) {
            Some(study::Action::ToggleStudy(study, is_selected)) => {
                if is_selected {
                    let already_exists = studies.iter().any(|s| s.is_same_type(&study));
                    if !already_exists {
                        studies.push(study);
                        studies_changed = true;
                    }
                } else {
                    let before = studies.len();
                    studies.retain(|s| !s.is_same_type(&study));
                    studies_changed = studies.len() != before;
                }
            }
            Some(study::Action::ConfigureStudy(study)) => {
                if let Some(existing_study) = studies.iter_mut().find(|s| s.is_same_type(&study)) {
                    *existing_study = study;
                    studies_changed = true;
                }
            }
            None => {}
        }

        if !studies_changed {
            return;
        }

        self.clear_overlay();
        self.clear_scale_labels();
        self.try_rebuild_instances();
    }

    pub fn set_visual_config(&mut self, config: data::chart::heatmap::Config) {
        if self.config == config {
            return;
        }

        let prev = self.config;
        self.config = config;

        let order_filter_changed =
            prev.order_size_filter.to_bits() != self.config.order_size_filter.to_bits();
        let trade_visual_changed = prev.trade_size_filter.to_bits()
            != self.config.trade_size_filter.to_bits()
            || prev.trade_size_scale != self.config.trade_size_scale;

        self.clear_overlay();
        self.clear_scale_labels();

        if trade_visual_changed || order_filter_changed {
            self.try_rebuild_instances();
        }

        if order_filter_changed {
            self.data_gen = self.data_gen.wrapping_add(1);
            // Delay full historical rebuild until user stops adjusting (debounce); force_historical
            // ensures the rebuild uses the updated filter when it fires.
            self.rebuild_signal.set_debouncing(true);
        }
    }

    pub fn toggle_indicator(&mut self, indicator: HeatmapIndicator) {
        if self.indicators.contains(&indicator) {
            self.indicators.retain(|i| i != &indicator);
        } else {
            self.indicators.push(indicator);
        }

        self.clear_overlay();
        self.clear_scale_labels();
        self.try_rebuild_instances();
    }

    fn cleanup_old_data(&mut self, aggr_time: u64) {
        // Keep CPU history aligned with what the ring can represent
        let keep_buckets: u64 = (self.depth_grid.tex_w().max(1)) as u64;

        let Some(latest_time) = self.latest_time else {
            return;
        };

        let keep_ms = keep_buckets.saturating_mul(aggr_time);
        let cutoff = latest_time.saturating_sub(keep_ms);
        let cutoff_rounded = (cutoff / aggr_time) * aggr_time;

        // Prune trades (TimeSeries datapoints are bucket timestamps)
        let keep = self.trades.datapoints.split_off(&cutoff_rounded);
        self.trades.datapoints = keep;

        // Prune HistoricalDepth to match the oldest remaining trade bucket (if any),
        // otherwise prune by cutoff directly
        if let Some(oldest_time) = self.trades.datapoints.keys().next().copied() {
            self.depth_history.cleanup_old_price_levels(oldest_time);
        } else {
            self.depth_history.cleanup_old_price_levels(cutoff_rounded);
        }
    }

    fn compute_view_window(&self, viewport_size: iced::Size) -> Option<ViewWindow> {
        let latest_time = self.latest_time?;
        let base_price = self.anchor.effective_base_price(self.base_price)?;

        let cfg = ViewConfig {
            depth_profile_col_width_px: DEPTH_PROFILE_WIDTH_PX,
            volume_strip_height_pct: STRIP_HEIGHT_FRAC,
            volume_profile_width_pct: VOLUME_PROFILE_WIDTH_PCT,
            max_steps_per_y_bin: i64::from(self.depth_grid.tex_h()),
        };

        let latest_render = self.anchor.effective_render_latest_time(latest_time);
        let latest_data_for_view = if self.anchor.is_paused() && latest_render > 0 {
            latest_render
        } else {
            latest_time
        };

        let input = ViewInputs {
            aggr_time: self.depth_history.aggr_time.max(1),
            latest_time_data: latest_data_for_view,
            latest_time_render: latest_render,
            base_price,
            step: self.step,
            cell: self.scene.cell,
        };

        ViewWindow::compute(cfg, &self.scene.camera, viewport_size, input)
    }

    /// Rebuild only CPU overlay instances (profile/volume/trades). This is intended to be
    /// cheap enough to run during interaction, unlike [`GridRing::rebuild_from_historical()`].
    fn rebuild_instances(&mut self, w: &ViewWindow) {
        let Some(palette) = &self.palette else {
            return;
        };
        let latest_time = match self.latest_time {
            Some(time) => time,
            None => return,
        };
        let base_price = match self.anchor.effective_base_price(self.base_price) {
            Some(price) => price,
            None => return,
        };

        // Keep trade-profile fade params synchronized with the same window used for
        // instance building so overlay labels anchor to current geometry immediately.
        self.scene.params.set_trade_fade(w);

        // If we are interacting (debounced), keep overlays on the *same* y-binning
        let mut effective_window = *w;
        let is_interacting = self.rebuild_signal.is_interacting();
        if is_interacting {
            let heatmap_steps_per_y_bin: i64 = self.scene.params.steps_per_y_bin();

            if effective_window.steps_per_y_bin != heatmap_steps_per_y_bin {
                effective_window.steps_per_y_bin = heatmap_steps_per_y_bin;
                effective_window.y_bin_h_world =
                    effective_window.row_h * (heatmap_steps_per_y_bin as f32);
            }
        }

        let volume_profile = self
            .studies
            .iter()
            .map(|study| match study {
                HeatmapStudy::VolumeProfile(profile) => profile,
            })
            .next();

        let latest_depth = self
            .depth_history
            .latest_order_runs(
                effective_window.highest,
                effective_window.lowest,
                latest_time,
            )
            .map(|(price, run)| (*price, run.qty, run.is_bid));

        let show_volume_indicator = self.indicators.contains(&HeatmapIndicator::Volume);

        let built = self.instances.build_instances(
            &effective_window,
            &self.trades,
            latest_depth,
            base_price,
            self.step,
            latest_time,
            self.anchor.scroll_ref_bucket(),
            palette,
            &self.config,
            &self.ticker_info.market_type(),
            volume_profile,
            show_volume_indicator,
        );

        let draw_list = built.draw_list();

        self.scene.set_circles(built.circles);
        self.scene.set_rectangles(built.rects);
        self.scene.set_draw_list(draw_list);
        self.clear_scale_labels();
    }

    fn try_rebuild_instances(&mut self) {
        let Some(size) = self.viewport_size_px() else {
            return;
        };
        let Some(w) = self.compute_view_window(size) else {
            return;
        };

        self.rebuild_instances(&w);
    }

    /// Returns `true` if the rebuild completed, `false` if it was skipped (no viewport / no data).
    /// Callers must only call `rebuild_signal.set_idle()` when this returns `true`; skipping
    /// `set_idle()` on `false` preserves any pending `force_historical` flag for the next frame.
    fn rebuild_all(&mut self, window: Option<ViewWindow>) -> bool {
        let Some(w) = window.or_else(|| {
            let size = self.viewport_size_px()?;
            self.compute_view_window(size)
        }) else {
            self.scene.clear();
            self.depth_grid.force_full_upload();
            return false;
        };

        let latest_time = match self.latest_time {
            Some(time) => time,
            None => return false,
        };
        let base_price = match self.anchor.effective_base_price(self.base_price) {
            Some(price) => price,
            None => return false,
        };

        let aggr_time: u64 = self.depth_history.aggr_time.max(1);
        let market_type = self.ticker_info.market_type();
        let size_in_quote_ccy =
            exchange::unit::qty::volume_size_unit() == exchange::SizeUnit::Quote;
        let order_size_filter = self.config.order_size_filter.max(0.0);

        let prev_steps_per_y_bin: i64 = self.scene.params.steps_per_y_bin();
        let new_steps_per_y_bin: i64 = w.steps_per_y_bin.max(1);

        self.scene.params.set_steps_per_y_bin(new_steps_per_y_bin);

        // Consume one-shot rebuild directive.
        let force_full_rebuild = self.rebuild_signal.take_force_historical();

        let recenter_target = self.scene.price_at_center(base_price, self.step);

        let need_full_rebuild = self.depth_grid.should_full_rebuild(
            prev_steps_per_y_bin,
            new_steps_per_y_bin,
            recenter_target,
            self.step,
            force_full_rebuild,
        );

        let effective_latest = if self.anchor.is_paused() {
            self.anchor.effective_render_latest_time(latest_time).max(1)
        } else {
            latest_time.max(1)
        };

        let final_latest = if need_full_rebuild {
            self.depth_grid.ensure_layout(aggr_time);

            let (oldest, newest) = self
                .depth_grid
                .horizon_time_window_ms(effective_latest, aggr_time);

            let (rebuild_highest, rebuild_lowest) = self.depth_grid.rebuild_price_bounds(
                recenter_target,
                self.step,
                new_steps_per_y_bin,
            );

            self.depth_grid.rebuild_from_historical(
                &self.depth_history,
                oldest,
                newest,
                recenter_target,
                self.step,
                new_steps_per_y_bin,
                self.qty_scale,
                rebuild_highest,
                rebuild_lowest,
                &market_type,
                size_in_quote_ccy,
                order_size_filter,
            );

            self.data_gen = self.data_gen.wrapping_add(1);

            newest
        } else {
            effective_latest
        };

        self.scene.sync_heatmap_texture(
            &self.depth_grid,
            base_price,
            self.step,
            self.qty_scale,
            final_latest,
            aggr_time,
            self.anchor.scroll_ref_bucket(),
        );

        // Guard for callers that trigger `rebuild_all` outside `invalidate` (e.g. resume
        // actions from `update`) which can lead to stale texture data but new y-mapping.
        self.scene
            .sync_heatmap_upload_from_grid(&mut self.depth_grid, need_full_rebuild);

        self.rebuild_instances(&w);
        true
    }

    fn rebuild_all_immediate(&mut self, w: Option<ViewWindow>, force_historical: bool) {
        self.rebuild_signal.set_immediate(force_historical);
        if self.rebuild_all(w) {
            self.rebuild_signal.set_idle();
        }
        // If rebuild_all returned false (no viewport / no data), leave the signal intact so
        // the next invalidate can retry with force_historical still set.
    }

    /// If the y-binning (steps_per_y_bin) would change, we must rebuild the heatmap texture.
    fn force_rebuild_if_ybin_changed(&mut self) {
        if self.rebuild_signal.is_interacting() {
            return;
        }

        let Some(viewport_size) = self.viewport_size_px() else {
            return;
        };
        let Some(w) = self.compute_view_window(viewport_size) else {
            return;
        };

        let cur_steps_per_y_bin: i64 = self.scene.params.steps_per_y_bin();
        if w.steps_per_y_bin != cur_steps_per_y_bin {
            self.rebuild_all_immediate(Some(w), false);
        }
    }

    fn viewport_size_px(&self) -> Option<iced::Size<f32>> {
        self.viewport.map(|r| r.size())
    }

    fn invalidate_with_view_window(&mut self, now_i: Instant, aggr_time: u64, w: &ViewWindow) {
        let latest_time = match self.latest_time {
            Some(time) => time,
            None => return,
        };
        let base_price = match self.anchor.effective_base_price(self.base_price) {
            Some(price) => price,
            None => return,
        };

        self.scene.params.set_trade_fade(w);
        {
            let recenter_target = self.scene.price_at_center(base_price, self.step);

            if self.depth_grid.should_recenter(recenter_target, self.step) {
                self.rebuild_signal.set_immediate(true);
            }
        }

        let aggr_time = aggr_time.max(1);
        let render_latest_time_eff = self.anchor.effective_render_latest_time(latest_time);
        let render_bucket: i64 = (render_latest_time_eff / aggr_time) as i64;

        let (scroll_ref_bucket, origin_x) = self.anchor.sync_scroll_ref_and_origin_x(render_bucket);

        let latest_time_for_heatmap = if self.anchor.is_paused() {
            render_latest_time_eff
        } else {
            latest_time
        };

        self.scene.params.set_origin_x(origin_x);
        self.scene.sync_heatmap_texture(
            &self.depth_grid,
            base_price,
            self.step,
            self.qty_scale,
            latest_time_for_heatmap,
            aggr_time,
            scroll_ref_bucket,
        );

        match self.rebuild_signal.decide(now_i) {
            RebuildDecision::Idle => {}
            RebuildDecision::OverlaysOnly => {
                self.rebuild_instances(w);
            }
            RebuildDecision::Full => {
                if self.rebuild_all(Some(*w)) {
                    self.rebuild_signal.set_idle();
                }
            }
        }

        self.scene
            .sync_heatmap_upload_from_grid(&mut self.depth_grid, false);

        self.update_depth_norm_and_params(*w, now_i);
    }

    fn update_depth_norm_and_params(&mut self, w: ViewWindow, now_i: Instant) {
        let latest_incl = w.latest_vis.saturating_add(w.aggr_time);
        let is_interacting = self.rebuild_signal.is_interacting();

        let norm_gen = if is_interacting || self.anchor.is_paused() {
            self.data_gen
        } else {
            latest_incl / w.aggr_time.max(1)
        };

        let denom = self.depth_norm.compute_throttled(
            &self.depth_history,
            &w,
            latest_incl,
            self.step,
            &self.ticker_info.market_type(),
            self.config.order_size_filter.max(0.0),
            norm_gen,
            now_i,
            is_interacting,
        );

        self.scene.params.set_depth_denom(denom);
    }

    fn mark_needs_full_upload_if_stalled(&mut self) {
        if let Some(last) = self.last_tick
            && last.elapsed() >= Duration::from_millis(HEATMAP_RESYNC_AFTER_STALL_MS)
        {
            self.depth_grid.force_full_upload();
        }
    }

    /// Resume paused mode immediately if x=0 becomes visible due user input (pan/zoom/reset).
    /// Returns whether a Paused -> Live transition happened.
    fn try_resume_if_x0_visible(&mut self) -> bool {
        if !self.anchor.is_paused() {
            return false;
        }

        let Some(viewport_size) = self.viewport_size_px() else {
            return false;
        };

        if !self.scene.profile_start_visible_x0(viewport_size) {
            return false;
        }

        let resumed = self.anchor.resume_to_live();
        if resumed {
            self.refresh_y_axis_gutter();
            self.clear_all_caches();
            self.rebuild_all_immediate(None, true);
        }

        resumed
    }

    fn update_live_ring_and_scene(&mut self, depth: &Depth, rounded_t: u64, is_interacting: bool) {
        let Some(base_price) = self.base_price else {
            return;
        };

        let steps_per_y_bin: i64 = self.scene.params.steps_per_y_bin();

        let recenter_target = if is_interacting {
            self.depth_grid.y_anchor_price().unwrap_or(base_price)
        } else {
            self.scene.price_at_center(base_price, self.step)
        };

        // If live ingest is about to recenter, schedule a forced rebuild-from-historical
        if self.depth_grid.should_recenter(recenter_target, self.step) {
            self.rebuild_signal.set_immediate(true);
        }

        self.depth_grid.ingest_snapshot(
            depth,
            rounded_t,
            self.step,
            self.qty_scale,
            recenter_target,
            steps_per_y_bin,
            &self.ticker_info.market_type(),
            exchange::unit::qty::volume_size_unit() == exchange::SizeUnit::Quote,
            self.config.order_size_filter.max(0.0),
        );
    }

    /// Apply follow-transition side effects after anchor timing/auto-follow has been updated.
    fn auto_update_anchor(&mut self, state_change: view::FollowStateChange) {
        if state_change == view::FollowStateChange::Unchanged {
            return;
        }

        self.refresh_y_axis_gutter();
        self.clear_all_caches();

        match state_change {
            view::FollowStateChange::ResumedToLive => self.rebuild_all_immediate(None, true),
            view::FollowStateChange::PausedFromLive => self.rebuild_signal.set_immediate(false),
            view::FollowStateChange::Unchanged => {}
        }
    }

    fn refresh_y_axis_gutter(&mut self) {
        self.y_axis_gutter = AxisYLabelCanvas::width(
            self.anchor.effective_base_price(self.base_price),
            self.ticker_info.min_ticksize,
        )
        .unwrap_or(DEFAULT_Y_AXIS_GUTTER);
    }

    // --- Cache invalidation helpers ---

    fn clear_x_axis(&self) {
        self.canvas_caches.x_axis.clear();
    }

    fn clear_y_axis(&self) {
        self.canvas_caches.y_axis.clear();
    }

    fn clear_overlay(&self) {
        self.canvas_caches.overlay.clear();
    }

    fn clear_scale_labels(&self) {
        self.canvas_caches.scale_labels.clear();
    }

    fn clear_all_caches(&self) {
        self.canvas_caches.x_axis.clear();
        self.canvas_caches.y_axis.clear();
        self.canvas_caches.overlay.clear();
        self.canvas_caches.scale_labels.clear();
    }

    fn clear_x_axis_motion(&self) {
        // mark_axis_x_motion = x_axis + overlay + scale_labels
        self.clear_x_axis();
        self.clear_overlay();
        self.clear_scale_labels();
    }

    fn clear_y_axis_motion(&self) {
        // mark_axis_y_motion = y_axis + overlay + scale_labels
        self.clear_y_axis();
        self.clear_overlay();
        self.clear_scale_labels();
    }

    fn clear_axes_motion(&self) {
        // mark_axes_motion = all 4
        self.clear_all_caches();
    }

    fn clear_cursor_moved(&self, x_axis_has_cursor_label: bool) {
        // mark_cursor_moved
        self.clear_y_axis();
        if x_axis_has_cursor_label {
            self.clear_x_axis();
        }
        self.clear_overlay();
        self.clear_scale_labels();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ticker_info_test() -> exchange::TickerInfo {
        exchange::TickerInfo::new(
            exchange::Ticker::new("BTCUSDT", exchange::adapter::Exchange::BinanceLinear),
            0.01,
            1.0,
            None,
        )
    }

    fn make_shader() -> HeatmapShader {
        HeatmapShader::new(
            data::chart::Basis::Time(exchange::Timeframe::MS1000),
            exchange::unit::PriceStep::from_f32_lossy(0.01),
            ticker_info_test(),
            vec![],
            vec![],
        )
    }

    #[test]
    fn camera_scale_returns_default_value() {
        let shader = make_shader();
        // Default camera scale is 100.0 (from Camera::default())
        assert_eq!(shader.camera_scale(), 100.0);
    }

    #[test]
    fn view_state_camera_scale_matches_shader() {
        let shader = make_shader();
        let vs = shader.view_state();
        assert_eq!(vs.camera_scale, shader.camera_scale());
    }

    #[test]
    fn view_state_captures_all_camera_fields() {
        let shader = make_shader();
        let vs = shader.view_state();
        assert_eq!(vs.camera_scale, shader.camera_scale());
        // Camera::default() starts at [0.0, 0.0]
        assert_eq!(vs.camera_offset, [0.0, 0.0]);
        // Cell dimensions must be positive
        assert!(vs.cell_width_world > 0.0);
        assert!(vs.cell_height_world > 0.0);
    }

    #[test]
    fn apply_view_state_restores_camera_fields() {
        let mut shader = make_shader();
        let vs = HeatmapViewState {
            camera_scale: 200.0,
            camera_offset: [5.0, -3.0],
            cell_width_world: 0.05,
            cell_height_world: 0.08,
        };
        shader.apply_view_state(vs);
        let restored = shader.view_state();
        assert_eq!(restored.camera_scale, 200.0);
        assert_eq!(restored.camera_offset, [5.0, -3.0]);
        assert_eq!(restored.cell_width_world, 0.05);
        assert_eq!(restored.cell_height_world, 0.08);
    }

    #[test]
    fn rebuild_signal_initial_is_idle() {
        let shader = make_shader();
        assert_eq!(
            shader.rebuild_signal.decide(Instant::now()),
            RebuildDecision::Idle
        );
    }

    #[test]
    fn rebuild_signal_immediate_returns_full() {
        let mut shader = make_shader();
        shader.rebuild_signal.set_immediate(false);
        assert_eq!(
            shader.rebuild_signal.decide(Instant::now()),
            RebuildDecision::Full
        );
    }

    #[test]
    fn rebuild_signal_debouncing_before_timeout_is_overlays_only() {
        let mut shader = make_shader();
        shader.rebuild_signal.set_debouncing(false);
        assert_eq!(
            shader.rebuild_signal.decide(Instant::now()),
            RebuildDecision::OverlaysOnly
        );
    }

    #[test]
    fn rebuild_signal_debouncing_after_timeout_is_full() {
        let mut shader = make_shader();
        let past = Instant::now() - Duration::from_millis(REBUILD_DEBOUNCE_MS + 10);
        shader.rebuild_signal = RebuildSignal::Debouncing {
            since: past,
            force_historical: false,
        };
        assert_eq!(
            shader.rebuild_signal.decide(Instant::now()),
            RebuildDecision::Full
        );
    }

    #[test]
    fn rebuild_signal_debouncing_at_exact_boundary_is_full() {
        // M-3: elapsed == REBUILD_DEBOUNCE_MS（ちょうど境界値）でも Full を返すことを確認。
        // `decide()` の判定は `>=` なので境界値は Full に含まれる。
        let mut shader = make_shader();
        // `saturating_duration_since` を使うため、過去 exactly REBUILD_DEBOUNCE_MS ms の
        // 時刻を設定する。実行タイミングのずれで elapsed が +1ms になる可能性を考慮し、
        // ちょうど REBUILD_DEBOUNCE_MS ms 前に設定する。
        let past = Instant::now() - Duration::from_millis(REBUILD_DEBOUNCE_MS);
        shader.rebuild_signal = RebuildSignal::Debouncing {
            since: past,
            force_historical: false,
        };
        // elapsed >= REBUILD_DEBOUNCE_MS であれば Full が返る。
        // 実行が瞬時ではないため elapsed は必ず >= REBUILD_DEBOUNCE_MS になる。
        assert_eq!(
            shader.rebuild_signal.decide(Instant::now()),
            RebuildDecision::Full,
            "elapsed == REBUILD_DEBOUNCE_MS の境界値でも Full を返す必要がある (M-3)"
        );
    }

    #[test]
    fn force_historical_preserved_across_signal_transitions() {
        let mut signal = RebuildSignal::Idle;
        signal.set_immediate(true); // Immediate { force_historical: true }
        signal.set_debouncing(false); // Debouncing { since: now, force_historical: true }
        assert!(signal.peek_force_historical());
        let consumed = signal.take_force_historical();
        assert!(consumed);
        assert!(!signal.peek_force_historical());
    }

    #[test]
    fn initial_anchor_is_live() {
        let shader = make_shader();
        assert!(!shader.anchor.is_paused());
    }

    #[test]
    fn zoom_at_changes_camera_scale_with_viewport() {
        let mut shader = make_shader();
        shader.viewport = Some(iced::Rectangle {
            x: 0.0,
            y: 0.0,
            width: 1000.0,
            height: 800.0,
        });
        let initial_scale = shader.camera_scale();
        shader.update(Message::ZoomAt {
            factor: 1.5,
            cursor: iced::Point { x: 500.0, y: 400.0 },
        });
        assert_ne!(
            shader.camera_scale(),
            initial_scale,
            "camera scale should change after zoom"
        );
    }

    #[test]
    fn pan_changes_camera_offset() {
        let mut shader = make_shader();
        let initial_offset = shader.scene.camera.offset;
        shader.update(Message::PanDeltaPx(iced::Vector { x: 10.0, y: 5.0 }));
        assert_ne!(
            shader.scene.camera.offset, initial_offset,
            "camera offset should change after pan"
        );
    }

    // --- Regression tests for B3 review findings ---

    /// H-2 regression: apply_view_state with viewport=None must preserve force_historical.
    /// Before fix: rebuild_all_immediate → rebuild_all early-return → set_idle() destroyed the flag.
    /// After fix: only set_immediate(true) is called; viewport=None defers the rebuild.
    #[test]
    fn apply_view_state_preserves_force_historical_without_viewport() {
        let mut shader = make_shader(); // viewport = None
        let vs = HeatmapViewState {
            camera_scale: 200.0,
            camera_offset: [1.0, -2.0],
            cell_width_world: 0.05,
            cell_height_world: 0.08,
        };
        shader.apply_view_state(vs);
        assert_eq!(
            shader.rebuild_signal.decide(Instant::now()),
            RebuildDecision::Full,
            "apply_view_state must leave signal as Immediate, not Idle (H-2)"
        );
        assert!(
            shader.rebuild_signal.peek_force_historical(),
            "force_historical must survive apply_view_state with no viewport (H-2)"
        );
    }

    /// H-4 regression: pause transition must be skipped when base_price is None.
    /// Before fix: Anchor::Paused { frozen_base_price: None } was valid, causing
    /// effective_base_price() to return None and the overlay to disappear silently.
    #[test]
    fn anchor_skips_pause_transition_when_no_base_price() {
        let mut anchor = view::Anchor::default();
        assert!(!anchor.is_paused());
        let changed = anchor.update_auto_follow(
            false, // x0 not visible → would normally trigger pause
            1000,
            0.0,
            None, // no price yet
        );
        assert!(!changed, "anchor must not transition to Paused without a base price (H-4)");
        assert!(!anchor.is_paused(), "anchor must remain Live when no price available (H-4)");
    }

    /// Verifies that pause transition DOES occur once a price is available.
    #[test]
    fn anchor_pauses_when_price_is_available() {
        let mut anchor = view::Anchor::default();
        let price = exchange::unit::Price::from_f32(50000.0);
        let changed = anchor.update_auto_follow(false, 1000, 0.0, Some(price));
        assert!(changed, "anchor should pause when x0 not visible and price exists");
        assert!(anchor.is_paused());
    }

    /// Guards against `decide()` panicking or returning Full when `since` is a future timestamp.
    /// saturating_duration_since should return Duration::ZERO → OverlaysOnly.
    #[test]
    fn rebuild_signal_future_since_returns_overlays_only() {
        let future_since = Instant::now() + Duration::from_secs(10);
        let signal = RebuildSignal::Debouncing {
            since: future_since,
            force_historical: false,
        };
        assert_eq!(
            signal.decide(Instant::now()),
            RebuildDecision::OverlaysOnly,
            "future since must yield OverlaysOnly via saturating_duration_since"
        );
    }
}
