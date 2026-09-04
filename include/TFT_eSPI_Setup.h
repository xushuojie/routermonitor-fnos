#pragma once

// Select the controller through a PlatformIO environment, not runtime detection.
#if defined(ST7789_DRIVER) && defined(ILI9341_DRIVER)
#error "Select exactly one display controller"
#elif defined(ST7789_DRIVER)
#define TFT_WIDTH  240
#define TFT_HEIGHT 240
#define TFT_CS     -1
#define SPI_FREQUENCY 40000000
#elif defined(ILI9341_DRIVER)
#define TFT_CS     PIN_D8
#define SPI_FREQUENCY 27000000
#else
#error "Select the nodemcuv2 or nodemcuv2_ili9341 PlatformIO environment"
#endif

#define TFT_MOSI PIN_D7
#define TFT_SCLK PIN_D5
#define TFT_DC   PIN_D3
#define TFT_RST  PIN_D4
#define TFT_BL   PIN_D1

#define LOAD_GLCD
#define LOAD_FONT2
#define LOAD_FONT4
#define LOAD_FONT6
#define LOAD_FONT7
#define LOAD_FONT8
#define LOAD_GFXFF
#define SMOOTH_FONT

#define SPI_READ_FREQUENCY  20000000
