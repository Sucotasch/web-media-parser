# Web Media Parser

A powerful application for parsing and downloading media files from websites with an intuitive GUI and intelligent URL prioritization.

## Features

- **Smart Media Discovery**: Automatically detects images, videos, and other media files on web pages
- **Intelligent URL Prioritization**: Prioritizes media URLs over navigation links, ensuring complete media extraction from each page
- **Full-size Image Detection**: Automatically discovers and downloads full-size versions of images (not just thumbnails)
- **Multi-threaded Processing**: Concurrent parsing and downloading for optimal performance
- **Site Pattern Support**: Extensible pattern system for site-specific optimizations
- **Modern GUI**: Dark-themed user interface built with PySide6
- **Download Management**: Progress tracking, pause/resume functionality, and duplicate detection
- **Configurable Settings**: Customizable depth limits, thread counts, file filters, and more


## Installation

### Requirements

- Python 3.8 or higher
- PySide6 (Qt6 for Python)
- Required Python packages (see requirements.txt)

### Quick Setup

1. Clone this repository:
```bash
git clone https://github.com/yourusername/yourreponame.git
cd yourreponame
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the application:
```bash
python main.py
```

### Building Executable

To create a standalone executable:

```bash
python build_exe.py
```

## Usage

1. **Launch the Application**: Run `python main.py`
2. **Enter URL**: Paste the URL of the website you want to parse
3. **Select Download Folder**: Choose where to save downloaded media
4. **Configure Settings** (optional):
   - Search depth (how many levels deep to follow links)
   - Number of parser and downloader threads
   - File type filters
   - Minimum image dimensions
5. **Start Parsing**: Click "Start" to begin media discovery and downloading

### Advanced Features

- **Pattern Management**: Add custom site patterns for better media detection
- **Stop Words**: Filter out unwanted URLs containing specific keywords
- **Domain Restrictions**: Limit parsing to the same domain as the starting URL
- **JavaScript Processing**: Enable dynamic content parsing for modern websites

## Architecture

The application uses a sophisticated multi-stage processing pipeline:

1. **URL Queue Management**: Intelligent prioritization ensures media URLs are processed before navigation
2. **Webpage Parsing**: HTML analysis to discover media files and follow relevant links
3. **Media Classification**: Automatic detection of full-size images vs thumbnails
4. **Download Optimization**: Concurrent downloading with progress tracking and error handling

### Key Components

- `src/parser/priority_url_queue.py`: Intelligent URL prioritization system
- `src/parser/webpage_parser.py`: HTML parsing and media discovery
- `src/parser/parser_manager.py`: Main coordination and workflow management
- `src/downloader/media_downloader.py`: Multi-threaded file downloading
- `src/gui/main_window.py`: User interface implementation

## Configuration

The application supports various configuration options:

- **Search Depth**: Control how deep to follow links (default: 3 levels)
- **Thread Counts**: Adjust parser and downloader thread counts for performance
- **File Filters**: Set minimum dimensions and file type restrictions
- **Custom Patterns**: Add site-specific parsing rules

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

### Development Setup

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Make your changes and add tests if applicable
4. Commit your changes: `git commit -am 'Add some feature'`
5. Push to the branch: `git push origin feature-name`
6. Submit a pull request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Changelog

### Version 1.0.0
- Initial release with core parsing and downloading functionality
- Intelligent URL prioritization system
- Full-size image detection
- Multi-threaded processing
- Modern GUI with dark theme

## Troubleshooting

### Common Issues

**Media files are not being detected:**
- Check if the website uses JavaScript to load content (enable dynamic processing)
- Verify the URL is accessible and contains media
- Check the application logs for parsing errors

**Download speeds are slow:**
- Increase the number of downloader threads in settings
- Check your internet connection
- Some sites may have rate limiting

**Application crashes on startup:**
- Ensure all dependencies are installed: `pip install -r requirements.txt`
- Check Python version compatibility (3.8+ required)
- Try running from command line to see error messages

## Support

If you encounter any issues or have questions:

1. Check the [Issues](https://github.com/yourusername/yourreponame/issues) page
2. Create a new issue with detailed information about the problem
3. Include log files and screenshots if relevant

## Acknowledgments

- Built with PySide6 for the GUI framework
- Uses aiohttp for asynchronous web requests
- BeautifulSoup for HTML parsing
- Thanks to all contributors and testers