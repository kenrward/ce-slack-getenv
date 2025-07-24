# Use an official Python runtime as a parent image
# Using a slim version reduces the overall image size
FROM python:3.11.8-slim-bullseye

# Update system packages to address vulnerabilities
RUN apt-get update && apt-get upgrade -y && apt-get dist-upgrade -y && apt-get autoremove -y && apt-get clean

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# --no-cache-dir reduces layer size, and --upgrade ensures packages are up to date
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Copy the rest of the application's code into the container at /app
COPY . .

# Define environment variable for the port
# Cloud Run automatically provides the PORT env var, which Gunicorn will use.
ENV PORT=8080

# Command to run the application using Gunicorn
# Gunicorn is a production-ready WSGI server.
# 'main:app' tells Gunicorn to look for an object named 'app' in a file named 'main.py'.
# The 'workers' and 'threads' flags can be tuned for performance.
# 'bind' tells Gunicorn to listen on all network interfaces inside the container on the specified port.
CMD exec gunicorn --workers 1 --threads 8 --bind 0.0.0.0:${PORT} main:app
