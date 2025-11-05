# Use lightweight Nginx image
FROM nginx:alpine

# Remove default Nginx config (optional)
RUN rm /etc/nginx/conf.d/default.conf

# Copy your static site files into Nginx's public directory
COPY . /usr/share/nginx/html

# Expose port 80 for web traffic
EXPOSE 80

# Start Nginx in foreground
CMD ["nginx", "-g", "daemon off;"]
git add Dockerfile
git commit -m "Add Dockerfile for Render static HTML deployment"
git push
