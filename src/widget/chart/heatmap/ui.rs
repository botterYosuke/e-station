pub mod axisx;
pub mod axisy;
pub mod overlay;

pub use super::Message;

const AXIS_TEXT_SIZE: f32 = 12.0;
const PAUSED_CTRL_LABEL_TEXT_SIZE: f32 = 11.0;

const PAUSED_CTRL_TEXT: &str = "Paused";
const PAUSED_CTRL_ICON_SIZE_FRAC: f32 = 0.032;
const PAUSED_CTRL_PADDING_FRAC: f32 = 0.02;
const PAUSED_CTRL_ICON_GAP_PX: f32 = 6.0;
const PAUSED_CTRL_BG_PAD_X: f32 = 6.0;
const PAUSED_CTRL_BG_PAD_Y: f32 = 3.0;
const PAUSED_CTRL_LABEL_WIDTH_FACTOR: f32 = 0.62;

#[derive(Debug, Clone, Copy)]
pub enum AxisZoomAnchor {
    /// Keep a specific world coordinate (along the active axis) fixed at a given screen position.
    World { world: f32, screen: f32 },
    /// Zoom anchored to the captured cursor position (along the active axis).
    Cursor { screen: f32 },
}

#[derive(Debug, Default, Clone, Copy)]
pub enum AxisInteraction {
    #[default]
    None,
    Panning {
        last_position: iced::Point,
        zoom_anchor: Option<AxisZoomAnchor>,
    },
}

#[derive(Debug, Clone, Copy)]
pub struct AxisState {
    pub interaction: AxisInteraction,
    pub previous_click: Option<iced_core::mouse::Click>,
}

impl Default for AxisState {
    fn default() -> Self {
        Self {
            interaction: AxisInteraction::None,
            previous_click: None,
        }
    }
}

#[derive(Default)]
pub struct CanvasCaches {
    pub y_axis: iced::widget::canvas::Cache,
    pub x_axis: iced::widget::canvas::Cache,
    pub overlay: iced::widget::canvas::Cache,
    pub scale_labels: iced::widget::canvas::Cache,
}

fn step_center_pos_from_world_y(world_y: f32, row_h: f32) -> f32 {
    (-(world_y) / row_h) - 0.5
}

fn world_y_for_step_center(step: i64, row_h: f32) -> f32 {
    -((step as f32 + 0.5) * row_h)
}

fn step_floor_from_world_y(world_y: f32, row_h: f32) -> i64 {
    ((-(world_y)) / row_h).floor() as i64
}

fn world_y_for_y_bin_center(y_bin: i64, steps_per_y_bin: i64, row_h: f32) -> f32 {
    -((y_bin as f32 + 0.5) * (steps_per_y_bin.max(1) as f32) * row_h)
}

fn paused_control_hovered(
    is_paused: bool,
    plot_bounds: Option<iced::Rectangle>,
    cursor: iced_core::mouse::Cursor,
) -> bool {
    if !is_paused {
        return false;
    }

    let Some(bounds) = plot_bounds else {
        return false;
    };

    let Some(pos) = cursor.position_over(bounds) else {
        return false;
    };

    paused_control_rect(bounds).contains(pos)
}

fn paused_control_rect(bounds: iced::Rectangle) -> iced::Rectangle {
    let icon_size = pause_icon_size(bounds);
    let padding = bounds.area().sqrt() * PAUSED_CTRL_PADDING_FRAC;
    let label_width = (PAUSED_CTRL_TEXT.len() as f32)
        * (PAUSED_CTRL_LABEL_TEXT_SIZE * PAUSED_CTRL_LABEL_WIDTH_FACTOR);

    let content_w = label_width + PAUSED_CTRL_ICON_GAP_PX + icon_size;
    let content_h = icon_size.max(PAUSED_CTRL_LABEL_TEXT_SIZE + 2.0);
    let rect_w = content_w + (PAUSED_CTRL_BG_PAD_X * 3.6);
    let rect_h = content_h + (PAUSED_CTRL_BG_PAD_Y * 2.0);

    iced::Rectangle {
        x: (bounds.x + bounds.width) - rect_w - padding,
        y: bounds.y + padding,
        width: rect_w,
        height: rect_h,
    }
}

fn pause_icon_size(bounds: iced::Rectangle) -> f32 {
    (PAUSED_CTRL_ICON_SIZE_FRAC * bounds.height).min(32.0)
}
