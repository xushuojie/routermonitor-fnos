#pragma once

// Hardware profile used by the 240x240 ESP8266 display in this project.
// Adjust these values if your board uses a different controller or wiring.
#define ILI9341_DRIVER

#define TFT_CS   PIN_D8
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

#define SPI_FREQUENCY       27000000
#define SPI_READ_FREQUENCY  20000000
